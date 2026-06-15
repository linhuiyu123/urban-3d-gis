"""
洪水淹没模拟引擎（真实水文：水体起涨 + 按地形高程连通漫滩 + 平滑水面）。

物理模型（“源头/浴缸”淹没模型）：
  - 把 water_level 理解为**洪水位 / 涨水高度 / 海平面高度**（米，相对水体基准面≈0）；
  - 以水系（河、湖、海岸）所在格子为种子，向 8 邻域做广度优先扩散；
  - **只淹“高程低于洪水位、且与水体连通”的格子**——高地天然挡水，内陆孤立低洼若不
    与水体连通也不会进水。于是：强降水使河湖暴涨、或海平面上升时，低洼河漫滩 / 沿海平原
    先淹，水位越高沿地形向上漫得越远，符合真实涨水过程。

高程来源：data/<area>/dem.npy（真实 DEM，米）优先；否则用与水系一致的合成地形场
（水体及河漫滩压低为低地，向丘陵方向抬升），保证离线也有合理、随水位单调变化的结果。
平滑：淹没栅格融合后做形态学平滑（buffer 外扩再内收）+ 简化，输出单一连续水面。
"""
from __future__ import annotations

import math
from collections import deque

import numpy as np
import shapely
from shapely.geometry import shape, box, mapping
from shapely.ops import unary_union

from ..config import CITIES, DATA_DIR
from ..data_loader import load_water
from . import geoutils


def _water_union(area: str):
    """把水系要素融合为一个几何（线状水体做细 buffer 成面）。"""
    feats = load_water(area).get("features", [])
    geoms = []
    for f in feats:
        try:
            g = shape(f["geometry"])
            if g.geom_type in ("LineString", "MultiLineString"):
                g = g.buffer(0.002)
            geoms.append(g)
        except Exception:
            continue
    return unary_union(geoms) if geoms else None


def _terrain_dem(area: str, lons: np.ndarray, lats: np.ndarray, water) -> np.ndarray:
    """与水系一致的合成高程场（米）。

    区域趋势：西/北部偏高（丘陵），东/南部偏低（平原、近水/近海）；叠加起伏；
    再把水体及两侧河漫滩“压低”到接近 0——水体处低地、地势沿离水方向抬升，
    洪水位越高沿地形向上淹没越广，物理上自洽。
    """
    min_lon, min_lat, max_lon, max_lat = CITIES[area]["bbox"]
    u = (lons - min_lon) / (max_lon - min_lon)        # 0西 → 1东
    v = (lats - min_lat) / (max_lat - min_lat)        # 0南 → 1北
    regional = 42.0 * (0.62 * (1.0 - u) + 0.38 * v)   # 西北高、东南低
    hills = 7.0 * np.sin(5 * np.pi * u) * np.cos(4 * np.pi * v) + 4.0 * np.sin(8 * np.pi * v)
    raw = np.clip(regional + hills, 0.0, None)
    if water is not None:
        dist_m = np.asarray(shapely.distance(water, shapely.points(lons, lats))) * 111000.0
        t = np.clip(dist_m / 500.0, 0.0, 1.0)         # 500m 河漫滩过渡带
        carve = t * t * (3.0 - 2.0 * t)               # smoothstep：近水→0，远→1
        return raw * carve
    return raw


def _load_elevation(area: str, lons: np.ndarray, lats: np.ndarray, water, n: int) -> np.ndarray:
    """高程（米，展平）：优先真实 DEM（dem.npy，形状须 n×n），否则一致合成地形。"""
    path = DATA_DIR / area / "dem.npy"
    if path.exists():
        try:
            dem = np.load(path)
            if dem.shape == (n, n):
                return dem.ravel().astype(float)
        except Exception:
            pass
    return _terrain_dem(area, lons, lats, water)


def simulate(area: str, water_level: float = 6.0, resolution: int = 100) -> dict:
    """模拟洪水位 water_level（米）下、从水体连通漫淹的范围（含平滑水面）。"""
    bbox = CITIES[area]["bbox"]
    lons, lats, dlon, dlat = geoutils.bbox_grid(bbox, resolution)
    n = resolution

    water = _water_union(area)
    elev = _load_elevation(area, lons, lats, water, n)

    below = elev < water_level                          # 低于洪水位的格子
    # 种子 = 水体所在格子（强降水使河湖暴涨 / 海平面上升，皆从水体开始漫淹）
    if water is not None:
        seed = np.asarray(shapely.contains_xy(water, lons, lats), bool)
    else:
        seed = np.zeros(n * n, bool)
    if not seed.any():
        seed = np.zeros(n * n, bool)
        seed[int(np.argmin(elev))] = True

    # 广度优先：从水体向 8 邻域扩散，只淹“低于洪水位且连通”的格子（高地挡水）
    flooded = np.zeros(n * n, dtype=bool)
    dq = deque(np.where(seed)[0].tolist())
    for i in dq:
        flooded[i] = True
    neigh = [-1, 1, -n, n, -n - 1, -n + 1, n - 1, n + 1]
    while dq:
        i = dq.popleft()
        r, c = divmod(i, n)
        for off in neigh:
            j = i + off
            if j < 0 or j >= n * n:
                continue
            rj, cj = divmod(j, n)
            if abs(rj - r) > 1 or abs(cj - c) > 1:      # 防止跨行环绕
                continue
            if not flooded[j] and below[j]:
                flooded[j] = True
                dq.append(j)

    # 融合淹没格子为面 + 平滑（外扩再内收）+ 简化；并入水体本身
    half_lon, half_lat = dlon / 2, dlat / 2
    cells = [box(float(lons[i]) - half_lon, float(lats[i]) - half_lat,
                 float(lons[i]) + half_lon, float(lats[i]) + half_lat)
             for i in np.where(flooded)[0]]
    if cells:
        merged = unary_union(cells)
        if water is not None:
            merged = unary_union([merged, water])       # 并入水体本身
        smooth_d = max(dlon, dlat) * 0.9
        smoothed = merged.buffer(smooth_d).buffer(-smooth_d * 0.85)
        smoothed = smoothed.simplify(max(dlon, dlat) * 0.4, preserve_topology=True)
        hazard = mapping(smoothed)
    else:
        hazard = None

    mid_lat = (bbox[1] + bbox[3]) / 2
    cell_km2 = (dlon * 111.0 * math.cos(math.radians(mid_lat))) * (dlat * 111.0)
    return {
        "hazard": hazard,
        "surface": {"type": "FeatureCollection",
                    "features": ([{"type": "Feature", "geometry": hazard,
                                   "properties": {"water_level": water_level}}] if hazard else [])},
        "meta": {"area": area, "water_level": water_level,
                 "flooded_cells": int(flooded.sum()), "resolution": resolution,
                 "flooded_area_km2": round(float(flooded.sum()) * cell_km2, 1),
                 "from_water": water is not None},
    }


def simulate_levels(area: str, target_level: float = 8.0, frames: int = 9,
                    resolution: int = 90) -> dict:
    """涨水过程：返回从低到 target_level 的一系列淹没帧，供前端做“水位逐渐升高”动画。

    每帧 = 一次 simulate 的结果（水位单调升高、淹没范围逐步扩大）。分辨率默认略低以加快多帧计算。
    """
    target_level = max(float(target_level), 1.0)
    levels = np.linspace(1.0, target_level, max(int(frames), 2))
    frame_list = []
    for wl in levels:
        r = simulate(area, float(wl), resolution)
        frame_list.append({
            "water_level": r["meta"]["water_level"],
            "flooded_area_km2": r["meta"]["flooded_area_km2"],
            "surface": r["surface"],
            "hazard": r["hazard"],
        })
    return {"frames": frame_list,
            "meta": {"area": area, "resolution": resolution,
                     "target_level": round(target_level, 1), "n_frames": len(frame_list)}}
