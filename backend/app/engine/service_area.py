"""
服务区 / 等时圈分析（网络分析）。

两种数据源（provider）：
  - offline（默认）：本地路网单源最短"时间"得到可达节点，凹包成面。已按城市拥堵车速校准、
    全城市可用、无外部依赖；步行/骑行为车行路网近似。
  - baidu：调用百度「批量算路」，对中心点四周采样点取**真实(含实时路况)耗时**再凹包成面。
    仅支持国内（杭州各研究区），消耗百度配额；海外或调用失败自动回退 offline 并注明。

为什么用凹包(concave hull)而非"逐段道路缓冲"：真实路网驾车大范围可达边段达十万级，逐段
buffer+union 会超时；凹包一次成形、零超时，比凸包紧——能削掉道路之间大片山体/水体的高估。
采样限点 _HULL_MAX_PTS 保证凹包计算快。
"""
from __future__ import annotations

import math
from functools import lru_cache

import shapely
from shapely.geometry import MultiPoint, mapping, shape
from shapely.ops import unary_union

import networkx as nx

from . import routing
from ..data_loader import load_water

_HULL_MAX_PTS = 4000        # 凹包采样上限：形状不需要每个点，限制点数以保证速度
_SMOOTH_DEG = 0.0008        # 轻微平滑（约 80m）


def _poly_area_km2(poly, lat0: float) -> float:
    return poly.area * (111.0 * math.cos(math.radians(lat0))) * 111.0


@lru_cache(maxsize=8)
def _water_clip(city: str):
    """该城市真实水体的合并几何（用于从服务区里挖掉湖泊/河流，如西湖）；无数据返回 None。"""
    geoms = []
    for f in load_water(city).get("features", []):
        g = f.get("geometry")
        if not g:
            continue
        try:
            s = shape(g)
            if not s.is_valid:
                s = s.buffer(0)
            if not s.is_empty:
                geoms.append(s)
        except Exception:
            continue
    if not geoms:
        return None
    return unary_union(geoms).simplify(0.0005, preserve_topology=True)   # ~50m 简化，加速裁剪


def _build_features(pts_time, bands, lat0: float, clip=None) -> list:
    """pts_time: [(lon,lat,minutes)...] → 各时间档凹包要素；clip（真实水体）会从面里挖掉。"""
    features = []
    for minutes in sorted(bands, reverse=True):
        pts = [(lon, lat) for lon, lat, t in pts_time if t <= minutes]
        n = len(pts)
        if n < 3:
            continue
        if n > _HULL_MAX_PTS:                        # 采样限点，保证凹包计算快、不超时
            pts = pts[::max(n // _HULL_MAX_PTS, 1)]
        mp = MultiPoint(pts)
        try:                                        # 凹包贴合可达点云；环境不支持则回退凸包
            poly = shapely.concave_hull(mp, ratio=0.35)
            if poly.is_empty or poly.geom_type not in ("Polygon", "MultiPolygon"):
                raise ValueError
        except Exception:
            poly = mp.convex_hull
        poly = poly.buffer(_SMOOTH_DEG)
        if clip is not None:                        # 挖掉真实水体（湖泊/河流），而非靠缓冲留碎洞
            try:
                poly = poly.difference(clip)
            except Exception:
                pass
        if poly.is_empty:
            continue
        features.append({
            "type": "Feature", "geometry": mapping(poly),
            "properties": {"minutes": minutes, "reachable_nodes": n,
                           "area_km2": round(_poly_area_km2(poly, lat0), 1)},
        })
    return features


def _summary(features) -> list:
    return sorted([{"minutes": f["properties"]["minutes"],
                    "area_km2": f["properties"]["area_km2"]} for f in features],
                  key=lambda s: s["minutes"])


def _offline(city: str, center: list[float], bands, mode: str) -> dict:
    """本地路网等时圈（凹包）。"""
    g = routing.build_graph(city)
    src = routing._nearest_within(g, center[0], center[1])
    if src is None:                                 # 中心点在研究区外/离路网过远
        return {"type": "FeatureCollection", "features": [],
                "meta": {"city": city, "error": "中心点不在研究区或离路网过远（请在城区道路附近取点）"}}
    wfn = routing._weight_fn(mode, "time")
    reach = nx.single_source_dijkstra_path_length(g, src, cutoff=max(bands), weight=wfn)
    pts_time = [(g.nodes[n]["lon"], g.nodes[n]["lat"], t) for n, t in reach.items()]
    features = _build_features(pts_time, bands, center[1], clip=_water_clip(city))
    if not features:
        return {"type": "FeatureCollection", "features": [],
                "meta": {"city": city, "error": "该点未连通到路网（换个靠路的点试试）"}}
    approx = mode in ("walk", "cycle", "transit")   # 当前仅车行路网，步行/骑行为近似
    return {"type": "FeatureCollection", "features": features,
            "meta": {"city": city, "center": center, "bands": bands, "summary": _summary(features),
                     "mode": mode, "mode_cn": routing.MODE_CN.get(mode, mode), "provider": "offline",
                     "approx": approx,
                     "note": ("步行/骑行按车行路网近似（缺人行道）；无实时路况"
                              if approx else "驾车已按城市拥堵车速估算，但无实时路况")}}


def isochrone(city: str, center: list[float], bands=(5, 10, 15),
              mode: str = "drive", provider: str = "offline") -> dict:
    """从 center([lon,lat]) 出发各时间档的服务范围（凹包）。provider=offline/baidu。"""
    bands = sorted({float(b) for b in (bands or []) if b and float(b) > 0})
    if not bands:                                   # 边界校验：空 bands 会触发 max([]) 异常
        return {"type": "FeatureCollection", "features": [],
                "meta": {"city": city, "error": "请至少选择一个有效的时间档（分钟数 > 0）"}}
    if not center or len(center) < 2:               # 坐标校验
        return {"type": "FeatureCollection", "features": [],
                "meta": {"city": city, "error": "中心点坐标格式应为 [lon, lat]"}}

    if provider == "baidu":
        if not city.startswith("hangzhou"):         # 百度仅支持国内研究区
            res = _offline(city, center, bands, mode)
            if res["features"]:
                res["meta"]["provider"] = "offline(海外回退)"
                res["meta"]["note"] = "百度实时路况仅支持国内研究区，已用离线估算；" + res["meta"].get("note", "")
            return res
        try:
            from . import baidu                       # 懒加载，避免无 AK 时影响离线
            return baidu.service_area(city, center, bands, mode)
        except Exception as e:                        # 百度失败 → 回退离线并注明
            res = _offline(city, center, bands, mode)
            if res["features"]:
                res["meta"]["provider"] = "offline(回退)"
                res["meta"]["note"] = f"百度调用失败已回退离线（{e}）；" + res["meta"].get("note", "")
            return res

    return _offline(city, center, bands, mode)
