"""
路径规划引擎（网络分析）。

把道路网（GeoJSON LineString）构建为 NetworkX 加权图：
  - 节点 = 道路折点（经纬度，按 6 位小数取整以便共享端点）；
  - 边权 = 路段长度（米）与通行时间（分钟，= 长度 / 速度）。

支持：
  - 通勤路径：起讫点间的最短距离 / 最短时间路径；
  - 撤离路径：从起点就近选择避难场所，并可避开危险区（如洪水淹没多边形）后重算。
"""
from __future__ import annotations

import math
from functools import lru_cache

import networkx as nx
import numpy as np
from scipy.spatial import cKDTree
from shapely.geometry import LineString, Point, shape

from ..data_loader import load_roads, load_shelters
from . import geoutils


def _haversine(lon1, lat1, lon2, lat2) -> float:
    """两经纬度点间球面距离（米）。"""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


@lru_cache(maxsize=4)
def build_graph(city: str) -> nx.Graph:
    """从道路 GeoJSON 构建无向加权图（带 length 米、time 分钟）。"""
    g = nx.Graph()
    fc = load_roads(city)
    for feat in fc.get("features", []):
        geom = feat.get("geometry") or {}
        if geom.get("type") != "LineString":
            continue
        speed = float(feat["properties"].get("speed", 40))  # km/h
        coords = geom["coordinates"]
        for (lon1, lat1), (lon2, lat2) in zip(coords[:-1], coords[1:]):
            n1 = (round(lon1, 6), round(lat1, 6))
            n2 = (round(lon2, 6), round(lat2, 6))
            length = _haversine(lon1, lat1, lon2, lat2)
            time_min = length / (speed * 1000 / 60)  # 分钟
            g.add_node(n1, lon=lon1, lat=lat1)
            g.add_node(n2, lon=lon2, lat=lat2)
            g.add_edge(n1, n2, length=length, time=time_min, speed=speed)
    return g


def _node_index(g: nx.Graph) -> tuple[cKDTree, list, float]:
    """为图节点建立 KDTree。

    经度按 cos(纬度) 缩放后再建树，使“最近”度量近似真实平面距离；
    否则在中高纬度直接用经纬度做欧氏距离会高估经度方向、把起讫点吸附到偏移的节点，
    造成路径起点明显跑偏。
    """
    nodes = list(g.nodes)
    coords = np.array(nodes, dtype=float)
    lat0 = float(coords[:, 1].mean()) if len(coords) else 0.0
    scale = math.cos(math.radians(lat0))
    metric = np.column_stack([coords[:, 0] * scale, coords[:, 1]])
    return cKDTree(metric), nodes, scale


def _nearest_node(g: nx.Graph, lon: float, lat: float):
    """找到离给定经纬度最近的图节点（按 cos 纬度校正的平面距离）。"""
    tree, nodes, scale = _node_index(g)
    _, idx = tree.query([lon * scale, lat])
    return nodes[idx]


def _nearest_within(g: nx.Graph, lon: float, lat: float, max_m: float = 3000.0):
    """最近节点；距离超过 max_m（米）则返回 None。

    防止研究区外的点被静默吸附到边界道路、返回一条看似正常实则错误的路线
    （例如把数十公里外的点直接吸到城内路网）。
    """
    node = _nearest_node(g, lon, lat)
    if _haversine(lon, lat, g.nodes[node]["lon"], g.nodes[node]["lat"]) > max_m:
        return None
    return node


def _graph_excluding(g: nx.Graph, hazard) -> nx.Graph:
    """返回去除与危险多边形相交边后的子图（用于避险绕行）。"""
    if hazard is None:
        return g
    h = g.copy()
    to_remove = []
    for u, v in h.edges():
        seg = LineString([(h.nodes[u]["lon"], h.nodes[u]["lat"]),
                          (h.nodes[v]["lon"], h.nodes[v]["lat"])])
        if seg.intersects(hazard):
            to_remove.append((u, v))
    h.remove_edges_from(to_remove)
    return h


# 各交通方式的平均速度（km/h）。drive=None 表示用道路自身限速（建图时已算入 time）。
MODE_SPEED_KMH = {"drive": None, "cycle": 16.0, "walk": 4.8, "transit": 22.0}
MODE_CN = {"drive": "驾车", "cycle": "骑行", "walk": "步行", "transit": "公交"}

# 驾车拥堵系数：实际平均车速约为道路自由流限速的该比例（含红绿灯、转向、拥堵）。
# 离线路网无实时路况，用它把"自由流时间"放大为更接近现实的耗时，避免服务区/等时圈
# "半小时开出几十公里"的高估。取值 0~1，越小越堵；杭州主城高峰可降到 0.45~0.5。
_DRIVE_CONGESTION = 0.6


def _edge_minutes(d: dict, mode: str) -> float:
    """某条边在指定交通方式下的通行时间（分钟）。"""
    spd = MODE_SPEED_KMH.get(mode)
    if spd is None:                                  # 驾车：道路限速(自由流) → 按拥堵系数放大耗时
        return d["time"] / _DRIVE_CONGESTION
    return d["length"] / (spd * 1000.0 / 60.0)


def _weight_fn(mode: str, optimize: str):
    """构造 networkx 边权函数：最短距离用长度，最快用该方式的通行时间。"""
    if optimize == "length":
        return lambda u, v, d: d["length"]
    return lambda u, v, d: _edge_minutes(d, mode)


def _path_to_geojson(g: nx.Graph, path: list, optimize: str, mode: str = "drive") -> dict:
    """把节点路径转为 GeoJSON LineString，并按交通方式统计总长度 / 总时间。"""
    coords = [[g.nodes[n]["lon"], g.nodes[n]["lat"]] for n in path]
    total_len = sum(g[u][v]["length"] for u, v in zip(path[:-1], path[1:]))
    total_time = sum(_edge_minutes(g[u][v], mode) for u, v in zip(path[:-1], path[1:]))
    return {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": coords},
        "properties": {
            "length_m": round(total_len, 1),
            "time_min": round(total_time, 1),
            "optimize": optimize,
            "mode": mode,
            "mode_cn": MODE_CN.get(mode, mode),
        },
    }


def _full_path(g: nx.Graph, nodes: list, wfn) -> list:
    """依次经过给定节点（起点→途径点→终点）的拼接路径。"""
    full = [nodes[0]]
    for a, b in zip(nodes[:-1], nodes[1:]):
        seg = nx.shortest_path(g, a, b, weight=wfn)
        full += seg[1:]                               # 去掉与上一段重复的连接点
    return full


def route(city: str, start: list[float], end: list[float],
          optimize: str = "time", hazard=None, mode: str = "drive",
          vias: list[list[float]] | None = None, alternatives: bool = False) -> dict:
    """
    通勤路径规划（支持交通方式、途径点、多条备选）。

    - optimize："time"（最快）或 "length"（最短）
    - mode：drive/cycle/walk/transit，影响通行时间
    - vias：可选途径点列表 [[lon,lat],...]，路线依次经过
    - alternatives=True：同时返回「最快」与「最短」两条作为备选（去重）
    - hazard：可选 shapely 多边形，路径将避开它
    """
    if len(start) < 2 or len(end) < 2:               # 坐标校验，避免空/残缺坐标导致 500
        return {"type": "FeatureCollection", "features": [],
                "meta": {"error": "起讫点坐标格式应为 [lon, lat]"}}
    g = _graph_excluding(build_graph(city), hazard)
    pts = [start] + list(vias or []) + [end]
    nodes = []
    for p in pts:
        n = _nearest_within(g, p[0], p[1])
        if n is None:                                 # 研究区外/离路网过远 → 不返回错误路线
            return {"type": "FeatureCollection", "features": [],
                    "meta": {"error": "有起点/终点/途经点不在研究区或离路网过远"}}
        nodes.append(n)

    # 备选时算「最快+最短」两条；否则按 optimize 单条。把用户当前选项排在前面。
    if alternatives:
        opts = ["time", "length"] if optimize == "time" else ["length", "time"]
    else:
        opts = [optimize]
    label = {"time": "最快", "length": "最短"}

    feats, seen = [], []
    try:
        for opt in opts:
            path = _full_path(g, nodes, _weight_fn(mode, opt))
            line = _path_to_geojson(g, path, opt, mode)
            key = (round(line["properties"]["length_m"]), round(line["properties"]["time_min"], 1))
            if key in seen:                           # 最快与最短若是同一条则去重
                continue
            seen.append(key)
            line["properties"]["rank"] = len(feats)
            line["properties"]["strategy_cn"] = label.get(opt, opt)
            feats.append(line)
    except nx.NetworkXNoPath:
        return {"type": "FeatureCollection", "features": [], "meta": {"error": "无可达路径"}}
    if not feats:
        return {"type": "FeatureCollection", "features": [], "meta": {"error": "无可达路径"}}

    alts = [{"strategy_cn": f["properties"]["strategy_cn"],
             "length_km": round(f["properties"]["length_m"] / 1000.0, 2),
             "time_min": f["properties"]["time_min"]} for f in feats]
    return {"type": "FeatureCollection", "features": feats,
            "meta": {"city": city, "optimize": optimize, "mode": mode,
                     "mode_cn": MODE_CN.get(mode, mode), "vias": len(vias or []), "alts": alts}}


def evacuate(city: str, start: list[float], hazard=None, mode: str = "drive") -> dict:
    """
    撤离路径规划：从起点前往**安全、可达、容量足**的最优避难场所（可选交通方式）。

    相比只挑"最近"，这里更贴近应急决策：
      1) 危险区(hazard)做 ~80m 缓冲，模拟道路封控/积水边缘，删除其上的道路；
      2) **排除落在危险区内的避难所**（自身被淹的避难所不可用）；
      3) 候选避难所按「可达时间 + 容量」综合评分择优，并在结果里给出推荐依据。
    """
    if len(start) < 2:                               # 坐标校验
        return {"type": "FeatureCollection", "features": [],
                "meta": {"error": "撤离起点坐标格式应为 [lon, lat]"}}
    # 危险区缓冲：约 0.0008°≈80m，模拟封控/积水边缘风险
    hz = hazard.buffer(0.0008) if hazard is not None else None
    g = _graph_excluding(build_graph(city), hz)
    s = _nearest_within(g, start[0], start[1])
    if s is None:
        return {"type": "FeatureCollection", "features": [],
                "meta": {"error": "撤离起点不在研究区或离路网过远"}}
    wfn = _weight_fn(mode, "time")

    shelters = load_shelters(city).get("features", [])
    cands = []
    for sh in shelters:
        lon, lat = sh["geometry"]["coordinates"][:2]
        if hz is not None and hz.contains(Point(lon, lat)):
            continue                                  # 避难所自身在危险区内 → 不可用
        node = _nearest_node(g, lon, lat)
        try:
            t = nx.shortest_path_length(g, s, node, weight=wfn)
        except nx.NetworkXNoPath:
            continue                                  # 道路被淹断、不可达
        cap = float(sh["properties"].get("capacity", 0) or 0)
        cands.append({"time": float(t), "node": node, "sh": sh, "cap": cap})

    if not cands:
        return {"type": "FeatureCollection", "features": [],
                "meta": {"error": "无安全可达的避难场所（可能都被淹没或道路中断）"}}

    # 综合评分：时间越短越好(权重0.6) + 容量越大越好(权重0.4)，均归一化
    tmax = max(c["time"] for c in cands) or 1.0
    cmax = max(c["cap"] for c in cands) or 1.0
    for c in cands:
        c["score"] = 0.6 * (1 - c["time"] / tmax) + 0.4 * (c["cap"] / cmax)
    best = max(cands, key=lambda c: c["score"])

    path = nx.shortest_path(g, s, best["node"], weight=wfn)
    line = _path_to_geojson(g, path, "time", mode)
    line["properties"]["type"] = "evacuation"
    sh = best["sh"]
    note = "" if mode == "drive" else f"；{MODE_CN.get(mode, mode)}为车行路网近似（非真实公交网）"
    return {
        "type": "FeatureCollection",
        "features": [line, {"type": "Feature", "geometry": sh["geometry"],
                            "properties": {**sh["properties"], "type": "shelter"}}],
        "meta": {"city": city, "shelter": sh["properties"].get("name"),
                 "capacity": int(best["cap"]), "time_min": round(best["time"], 1),
                 "candidates": len(cands), "mode": mode, "mode_cn": MODE_CN.get(mode, mode),
                 "reason": f"在 {len(cands)} 个安全可达避难所中综合最优"
                           f"（{round(best['time'], 1)} 分钟可达，容量 {int(best['cap'])} 人）" + note},
    }


def to_shape(geojson_geom: dict | None):
    """把 GeoJSON 几何转为 shapely 对象（供 hazard 使用）。"""
    return shape(geojson_geom) if geojson_geom else None
