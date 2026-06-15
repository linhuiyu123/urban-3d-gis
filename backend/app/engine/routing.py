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
from shapely.geometry import LineString, shape

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


def _edge_minutes(d: dict, mode: str) -> float:
    """某条边在指定交通方式下的通行时间（分钟）。"""
    spd = MODE_SPEED_KMH.get(mode)
    if spd is None:                                  # 驾车：用道路限速
        return d["time"]
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


def route(city: str, start: list[float], end: list[float],
          optimize: str = "time", hazard=None, mode: str = "drive",
          vias: list[list[float]] | None = None) -> dict:
    """
    通勤路径规划（支持交通方式与途径点）。

    - start / end：[lon, lat]
    - optimize："time"（最快）或 "length"（最短）
    - mode：drive/cycle/walk/transit，影响通行时间
    - vias：可选途径点列表 [[lon,lat],...]，路线依次经过
    - hazard：可选 shapely 多边形，路径将避开它
    """
    g = _graph_excluding(build_graph(city), hazard)
    wfn = _weight_fn(mode, optimize)
    pts = [start] + list(vias or []) + [end]
    nodes = [_nearest_node(g, p[0], p[1]) for p in pts]
    try:
        full_path = [nodes[0]]
        for a, b in zip(nodes[:-1], nodes[1:]):
            seg = nx.shortest_path(g, a, b, weight=wfn)
            full_path += seg[1:]                      # 去掉与上一段重复的连接点
    except nx.NetworkXNoPath:
        return {"type": "FeatureCollection", "features": [], "meta": {"error": "无可达路径"}}
    return {"type": "FeatureCollection", "features": [_path_to_geojson(g, full_path, optimize, mode)],
            "meta": {"city": city, "optimize": optimize, "mode": mode,
                     "mode_cn": MODE_CN.get(mode, mode), "vias": len(vias or [])}}


def evacuate(city: str, start: list[float], hazard=None, mode: str = "drive") -> dict:
    """
    撤离路径规划：从起点出发，避开危险区，前往可达的最近避难场所（可选交通方式）。

    返回选中的避难场所点 + 撤离路线。
    """
    g = _graph_excluding(build_graph(city), hazard)
    s = _nearest_node(g, start[0], start[1])
    wfn = _weight_fn(mode, "time")

    shelters = load_shelters(city).get("features", [])
    best = None
    for sh in shelters:
        lon, lat = sh["geometry"]["coordinates"][:2]
        node = _nearest_node(g, lon, lat)
        try:
            length = nx.shortest_path_length(g, s, node, weight=wfn)
        except nx.NetworkXNoPath:
            continue
        if best is None or length < best[0]:
            best = (length, node, sh)

    if best is None:
        return {"type": "FeatureCollection", "features": [], "meta": {"error": "无可达避难场所"}}

    _, target_node, shelter = best
    path = nx.shortest_path(g, s, target_node, weight=wfn)
    line = _path_to_geojson(g, path, "time", mode)
    line["properties"]["type"] = "evacuation"
    return {
        "type": "FeatureCollection",
        "features": [line, {"type": "Feature", "geometry": shelter["geometry"],
                            "properties": {**shelter["properties"], "type": "shelter"}}],
        "meta": {"city": city, "shelter": shelter["properties"].get("name"),
                 "mode": mode, "mode_cn": MODE_CN.get(mode, mode)},
    }


def to_shape(geojson_geom: dict | None):
    """把 GeoJSON 几何转为 shapely 对象（供 hazard 使用）。"""
    return shape(geojson_geom) if geojson_geom else None
