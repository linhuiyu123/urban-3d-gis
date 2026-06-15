"""
服务区 / 等时圈分析（网络分析）。

从一个设施点出发，沿真实路网计算 N 分钟内可达的所有路段，
再把可达节点聚合成等时圈多边形（凸包 + 缓冲平滑）。
可一次输出多个时间档（如 5/10/15 分钟）形成嵌套等时圈。
"""
from __future__ import annotations

import networkx as nx
from shapely.geometry import MultiPoint, mapping

from . import routing


def isochrone(city: str, center: list[float], bands=(5, 10, 15), mode: str = "drive") -> dict:
    """
    计算从 center([lon,lat]) 出发的多档等时圈（可选交通方式）。

    返回 FeatureCollection，每个时间档一个 Polygon，properties.minutes 标注分钟数。
    外层（时间长）先画、内层后画，前端按 minutes 着色即可形成层次。
    """
    g = routing.build_graph(city)
    src = routing._nearest_node(g, center[0], center[1])

    # 单源最短"时间"距离，按交通方式计权，cutoff 取最大档
    wfn = routing._weight_fn(mode, "time")
    lengths = nx.single_source_dijkstra_path_length(g, src, cutoff=max(bands), weight=wfn)

    features = []
    for minutes in sorted(bands, reverse=True):    # 从大到小，便于前端叠放
        pts = [(g.nodes[n]["lon"], g.nodes[n]["lat"]) for n, t in lengths.items() if t <= minutes]
        if len(pts) < 3:
            continue
        # 凸包近似可达范围；buffer 做轻微平滑（约 150 米）
        poly = MultiPoint(pts).convex_hull.buffer(0.0015)
        features.append({
            "type": "Feature",
            "geometry": mapping(poly),
            "properties": {"minutes": minutes, "reachable_nodes": len(pts)},
        })

    summary = sorted(
        [{"minutes": f["properties"]["minutes"],
          "reachable_nodes": f["properties"]["reachable_nodes"]} for f in features],
        key=lambda s: s["minutes"])
    return {"type": "FeatureCollection", "features": features,
            "meta": {"city": city, "center": center, "bands": list(bands), "summary": summary,
                     "mode": mode, "mode_cn": routing.MODE_CN.get(mode, mode)}}
