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
from functools import lru_cache
from typing import NamedTuple

import numpy as np
import shapely
from shapely.geometry import shape, box, mapping
from shapely.ops import transform, unary_union
from pyproj import Transformer

from ..config import CITIES, DATA_DIR
from ..data_loader import load_water
from . import geoutils


def _mtime(path) -> float:
    """返回文件更新时间，用作洪水内部缓存失效键。"""
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _water_stamp(area: str) -> float:
    return _mtime(DATA_DIR / area / "water.geojson")


def _dem_stamp(area: str) -> float:
    return _mtime(DATA_DIR / area / "dem.npy")


def _elevation_source_label(source: str) -> str:
    if source == "dem.npy":
        return "真实 DEM"
    if source.startswith("dem.npy_resampled_from_"):
        return "真实 DEM（已重采样）"
    return "合成地形"


class _FloodContext(NamedTuple):
    area: str
    resolution: int
    xs: np.ndarray
    ys: np.ndarray
    dx_m: float
    dy_m: float
    water_utm: object
    bbox_utm: object
    elev: np.ndarray
    elevation_source: str
    warnings: tuple[str, ...]
    seed: np.ndarray
    datum: float
    from_water: bool


@lru_cache(maxsize=16)
def _projectors(area: str):
    """返回 WGS84 <-> 城市 UTM 的 shapely transform 函数。"""
    epsg = CITIES[area]["utm_epsg"]
    to_utm = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
    to_wgs = Transformer.from_crs(f"EPSG:{epsg}", "EPSG:4326", always_xy=True)
    return to_utm.transform, to_wgs.transform


@lru_cache(maxsize=16)
def _bbox_utm(area: str):
    """研究区 bbox 的 UTM 面，用于裁剪外溢的水系几何。"""
    min_lon, min_lat, max_lon, max_lat = CITIES[area]["bbox"]
    to_utm, _ = _projectors(area)
    return transform(to_utm, box(min_lon, min_lat, max_lon, max_lat))


@lru_cache(maxsize=16)
def _water_union(area: str, water_stamp: float):
    """把水系要素融合为一个几何。

    水系原始数据是 WGS84 经纬度。旧实现直接用“度”做 buffer / distance，
    在不同纬度和不同研究区尺度下会产生明显误差；这里统一投影到 UTM 米制坐标后
    再处理线状水体。返回值为 (是否有水系, UTM 水系几何)。
    """
    load_water_uncached = getattr(load_water, "__wrapped__", load_water)
    feats = load_water_uncached(area).get("features", [])
    to_utm, _ = _projectors(area)
    bbox_utm = _bbox_utm(area)
    geoms = []
    for f in feats:
        try:
            g = transform(to_utm, shape(f["geometry"]))
            if g.geom_type in ("LineString", "MultiLineString"):
                g = g.buffer(45.0, cap_style=2, join_style=2)
            g = g.intersection(bbox_utm)
            if not g.is_empty:
                geoms.append(g)
        except Exception:
            continue
    if not geoms:
        return None, None
    water_utm = unary_union(geoms)
    return True, water_utm


def _terrain_dem(area: str, lons: np.ndarray, lats: np.ndarray,
                 xs: np.ndarray, ys: np.ndarray, water_utm) -> np.ndarray:
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
    if water_utm is not None:
        dist_m = np.asarray(shapely.distance(water_utm, shapely.points(xs, ys)))
        t = np.clip(dist_m / 900.0, 0.0, 1.0)         # 900m 河漫滩过渡带
        carve = t * t * (3.0 - 2.0 * t)               # smoothstep：近水→0，远→1
        floodplain = 0.006 * dist_m                   # 离水越远，地势缓慢抬升
        return np.minimum(raw, raw * carve + floodplain)
    return raw


def _fill_invalid_dem(dem: np.ndarray) -> tuple[np.ndarray, list[str]]:
    """填补 dem.npy 里的 NaN/inf，避免水位基准和连通扩散被坏值污染。"""
    warnings: list[str] = []
    invalid = ~np.isfinite(dem)
    if not invalid.any():
        return dem, warnings
    valid = ~invalid
    if not valid.any():
        return dem, ["dem.npy has no finite elevation values"]

    warnings.append(f"dem.npy had {int(invalid.sum())} invalid cells; filled before simulation")
    try:
        from scipy import ndimage

        _, indices = ndimage.distance_transform_edt(invalid, return_indices=True)
        return dem[tuple(indices)], warnings
    except Exception:
        filled = dem.copy()
        filled[invalid] = float(np.nanmean(dem))
        return filled, warnings


def _resize_dem(dem: np.ndarray, n: int) -> np.ndarray:
    """把已有 DEM 重采样到请求分辨率，保持前端分辨率滑块与真实 DEM 兼容。"""
    if dem.shape == (n, n):
        return dem
    rows = np.linspace(0, dem.shape[0] - 1, n).round().astype(int)
    cols = np.linspace(0, dem.shape[1] - 1, n).round().astype(int)
    try:
        from scipy import ndimage

        zoom_y = n / dem.shape[0]
        zoom_x = n / dem.shape[1]
        resized = ndimage.zoom(dem, (zoom_y, zoom_x), order=1)
        if resized.shape == (n, n):
            return resized
        return resized[np.ix_(
            np.linspace(0, resized.shape[0] - 1, n).round().astype(int),
            np.linspace(0, resized.shape[1] - 1, n).round().astype(int),
        )]
    except Exception:
        return dem[np.ix_(rows, cols)]


def _load_elevation(area: str, lons: np.ndarray, lats: np.ndarray,
                    xs: np.ndarray, ys: np.ndarray, water_utm, n: int) -> tuple[np.ndarray, str, list[str]]:
    """高程（米，展平）：优先真实 DEM，必要时重采样；失败才回退合成地形。"""
    path = DATA_DIR / area / "dem.npy"
    warnings: list[str] = []
    if path.exists():
        try:
            dem = np.load(path).astype(float)
            if dem.ndim != 2 or min(dem.shape) < 2:
                raise ValueError(f"dem.npy must be a 2D array with at least 2x2 cells, got {dem.shape}")
            dem, fill_warnings = _fill_invalid_dem(dem)
            warnings.extend(fill_warnings)
            if not np.isfinite(dem).any():
                raise ValueError("dem.npy has no finite elevation values")
            original_shape = dem.shape
            dem = _resize_dem(dem, n)
            source = "dem.npy" if original_shape == (n, n) else f"dem.npy_resampled_from_{original_shape[0]}x{original_shape[1]}"
            return dem.ravel().astype(float), source, warnings
        except Exception as exc:
            warnings.append(f"dem.npy ignored: {exc}")
    return _terrain_dem(area, lons, lats, xs, ys, water_utm), "synthetic", warnings


def _cell_size_m(xs: np.ndarray, ys: np.ndarray, n: int) -> tuple[float, float, float]:
    """估算栅格单元在 UTM 下的宽、高和对角线长度。"""
    gx = xs.reshape(n, n)
    gy = ys.reshape(n, n)
    dx = float(np.median(np.hypot(np.diff(gx, axis=1), np.diff(gy, axis=1)))) if n > 1 else 1.0
    dy = float(np.median(np.hypot(np.diff(gx, axis=0), np.diff(gy, axis=0)))) if n > 1 else 1.0
    diag = math.hypot(dx, dy)
    return max(dx, 1.0), max(dy, 1.0), max(diag, 1.0)


def _seed_cells(water_utm, xs: np.ndarray, ys: np.ndarray, elev: np.ndarray,
                cell_diag_m: float) -> np.ndarray:
    """确定洪水起涨种子。

    优先取水体内的格心；若水体很窄、格心没有落入水体，则取与水体相交概率最高的近水格。
    不再直接退化到“全区最低点”，除非完全没有水系。
    """
    if water_utm is None:
        seed = np.zeros(xs.size, bool)
        seed[int(np.argmin(elev))] = True
        return seed

    seed = np.asarray(shapely.contains_xy(water_utm, xs, ys), bool)
    if seed.any():
        return seed

    dist_m = np.asarray(shapely.distance(water_utm, shapely.points(xs, ys)))
    near = dist_m <= max(cell_diag_m * 0.75, 80.0)
    if near.any():
        return near

    seed = np.zeros(xs.size, bool)
    seed[int(np.argmin(dist_m))] = True
    return seed


def _water_surface_level(elev: np.ndarray, seed: np.ndarray, water_level: float) -> tuple[float, float]:
    """把用户输入的水位解释为相对水体基准面的涨水高度。

    真实 DEM 常带有绝对高程基准，水体格不一定接近 0m。取水体种子格的低分位作为
    local datum，再叠加 water_level，可避免“同样 6m 在不同 DEM 基准下完全失真”。
    """
    if seed.any():
        base = float(np.nanpercentile(elev[seed], 10))
    else:
        base = float(np.nanmin(elev))
    return base, base + max(float(water_level), 0.0)


def _flood_fill(below: np.ndarray, seed: np.ndarray, n: int) -> np.ndarray:
    """从水体种子向 8 邻域连通扩散，只通过低于水面的格子。"""
    try:
        from scipy import ndimage

        labels, _ = ndimage.label(below.reshape(n, n), structure=np.ones((3, 3), dtype=np.uint8))
        seed_labels = np.unique(labels.ravel()[seed])
        seed_labels = seed_labels[seed_labels != 0]
        if seed_labels.size:
            return np.isin(labels, seed_labels).ravel()
        return np.zeros(n * n, dtype=bool)
    except Exception:
        pass

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
    return flooded


def _flood_rectangles(flooded: np.ndarray, xs: np.ndarray, ys: np.ndarray,
                      dx_m: float, dy_m: float, n: int) -> list:
    """把连续淹没格压缩为矩形，减少后续 unary_union 的几何数量。"""
    grid = flooded.reshape(n, n)
    active: dict[tuple[int, int], int] = {}
    rects: list[tuple[int, int, int, int]] = []

    for r in range(n):
        row = grid[r]
        starts = np.flatnonzero(row & ~np.r_[False, row[:-1]])
        ends = np.flatnonzero(row & ~np.r_[row[1:], False])
        next_active: dict[tuple[int, int], int] = {}
        for c0, c1 in zip(starts.tolist(), ends.tolist()):
            key = (c0, c1)
            next_active[key] = active.pop(key, r)
        for (c0, c1), r0 in active.items():
            rects.append((r0, r - 1, c0, c1))
        active = next_active
    for (c0, c1), r0 in active.items():
        rects.append((r0, n - 1, c0, c1))

    gx = xs.reshape(n, n)
    gy = ys.reshape(n, n)
    half_x, half_y = dx_m / 2, dy_m / 2
    return [
        box(float(gx[r0, c0]) - half_x, float(gy[r0, c0]) - half_y,
            float(gx[r1, c1]) + half_x, float(gy[r1, c1]) + half_y)
        for r0, r1, c0, c1 in rects
    ]


@lru_cache(maxsize=32)
def _context(area: str, resolution: int, water_stamp: float, dem_stamp: float) -> _FloodContext:
    """构建只和区域/分辨率有关的洪水分析上下文。"""
    bbox = CITIES[area]["bbox"]
    lons, lats, _, _ = geoutils.bbox_grid(bbox, resolution)
    n = resolution
    xs, ys = geoutils.lonlat_to_utm(area, lons, lats)
    dx_m, dy_m, cell_diag_m = _cell_size_m(xs, ys, n)

    from_water, water_utm = _water_union(area, water_stamp)
    elev, elevation_source, warnings = _load_elevation(area, lons, lats, xs, ys, water_utm, n)
    seed = _seed_cells(water_utm, xs, ys, elev, cell_diag_m)
    datum, _ = _water_surface_level(elev, seed, 0.0)

    return _FloodContext(
        area=area,
        resolution=resolution,
        xs=xs,
        ys=ys,
        dx_m=dx_m,
        dy_m=dy_m,
        water_utm=water_utm,
        bbox_utm=_bbox_utm(area),
        elev=elev,
        elevation_source=elevation_source,
        warnings=tuple(warnings),
        seed=seed,
        datum=datum,
        from_water=bool(from_water),
    )


def _simulate_with_context(ctx: _FloodContext, water_level: float) -> dict:
    """在已准备好的上下文上计算某个水位，供单次模拟和动画复用。"""
    n = ctx.resolution
    surface_level = ctx.datum + max(float(water_level), 0.0)
    below = ctx.elev <= surface_level                   # 低于当前水面的候选格子
    below[ctx.seed] = True                              # 水体本身始终作为连通水面
    flooded = _flood_fill(below, ctx.seed, n)

    # 融合淹没格子为面 + 米制平滑；最后转回 WGS84 GeoJSON。
    _, to_wgs = _projectors(ctx.area)
    cells = _flood_rectangles(flooded, ctx.xs, ctx.ys, ctx.dx_m, ctx.dy_m, n)
    if cells:
        merged = unary_union(cells)
        if ctx.water_utm is not None:
            merged = unary_union([merged, ctx.water_utm])   # 并入水体本身
        smooth_d = min(max(ctx.dx_m, ctx.dy_m) * 0.7, 140.0)
        smoothed = merged.buffer(smooth_d).buffer(-smooth_d * 0.85)
        smoothed = smoothed.simplify(max(ctx.dx_m, ctx.dy_m) * 0.35, preserve_topology=True)
        smoothed = smoothed.intersection(ctx.bbox_utm)
        if smoothed.is_empty:
            hazard_utm = None
            hazard = None
        else:
            hazard_utm = smoothed
            hazard = mapping(transform(to_wgs, smoothed))
    else:
        hazard_utm = None
        hazard = None

    flooded_area_km2 = (hazard_utm.area / 1_000_000.0) if hazard_utm is not None else 0.0
    return {
        "hazard": hazard,
        "surface": {"type": "FeatureCollection",
                    "features": ([{"type": "Feature", "geometry": hazard,
                                   "properties": {"water_level": water_level}}] if hazard else [])},
        "meta": {"area": ctx.area, "water_level": water_level,
                 "flooded_cells": int(flooded.sum()), "resolution": ctx.resolution,
                 "flooded_area_km2": round(flooded_area_km2, 1),
                 "from_water": ctx.from_water,
                 "elevation_source": ctx.elevation_source,
                 "elevation_source_label": _elevation_source_label(ctx.elevation_source),
                 "dem_path": str(DATA_DIR / ctx.area / "dem.npy"),
                 "water_datum_m": round(ctx.datum, 2),
                 "surface_elevation_m": round(surface_level, 2),
                 "warnings": list(ctx.warnings)},
    }


def simulate(area: str, water_level: float = 6.0, resolution: int = 100) -> dict:
    """模拟洪水位 water_level（米）下、从水体连通漫淹的范围（含平滑水面）。"""
    return _simulate_with_context(
        _context(area, int(resolution), _water_stamp(area), _dem_stamp(area)),
        water_level,
    )


def simulate_levels(area: str, target_level: float = 8.0, frames: int = 9,
                    resolution: int = 90) -> dict:
    """涨水过程：返回从低到 target_level 的一系列淹没帧，供前端做“水位逐渐升高”动画。

    每帧 = 一次 simulate 的结果（水位单调升高、淹没范围逐步扩大）。分辨率默认略低以加快多帧计算。
    """
    target_level = max(float(target_level), 1.0)
    levels = np.linspace(1.0, target_level, max(int(frames), 2))
    ctx = _context(area, int(resolution), _water_stamp(area), _dem_stamp(area))
    frame_list = []
    for wl in levels:
        r = _simulate_with_context(ctx, float(wl))
        frame_list.append({
            "water_level": r["meta"]["water_level"],
            "flooded_area_km2": r["meta"]["flooded_area_km2"],
            "surface": r["surface"],
            "hazard": r["hazard"],
        })
    return {"frames": frame_list,
            "meta": {"area": area, "resolution": resolution,
                     "target_level": round(target_level, 1), "n_frames": len(frame_list)}}
