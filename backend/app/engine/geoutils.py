"""
地理计算工具。

WGS84 经纬度（度）不能直接做"米"为单位的缓冲与距离计算，
因此统一投影到对应城市的 UTM 带（米制平面坐标）再计算。
本模块封装经纬度 <-> UTM 的相互转换，以及若干通用几何工具。
"""
from __future__ import annotations

import numpy as np
from pyproj import Transformer

from ..config import CITIES


def _transformers(city: str) -> tuple[Transformer, Transformer]:
    """构造某城市的 (经纬度->UTM, UTM->经纬度) 转换器。"""
    epsg = CITIES[city]["utm_epsg"]
    to_utm = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
    to_wgs = Transformer.from_crs(f"EPSG:{epsg}", "EPSG:4326", always_xy=True)
    return to_utm, to_wgs


def lonlat_to_utm(city: str, lon, lat):
    """经纬度（可为数组）转 UTM 平面坐标，返回 (x, y)，单位米。"""
    to_utm, _ = _transformers(city)
    return to_utm.transform(lon, lat)


def utm_to_lonlat(city: str, x, y):
    """UTM 平面坐标转回经纬度。"""
    _, to_wgs = _transformers(city)
    return to_wgs.transform(x, y)


def coords_array(features: list[dict]) -> np.ndarray:
    """从点要素列表提取经纬度坐标数组，形状 (N, 2)。"""
    pts = []
    for f in features:
        g = f.get("geometry") or {}
        if g.get("type") == "Point":
            pts.append(g["coordinates"][:2])
    return np.array(pts, dtype=float) if pts else np.empty((0, 2))


def bbox_grid(bbox: list[float], n: int = 60) -> tuple[np.ndarray, np.ndarray, float, float]:
    """
    在 bbox 范围内生成 n×n 规则网格中心点。
    返回 (lons, lats, dlon, dlat)，其中 lons/lats 为展平后的一维数组，
    dlon/dlat 为单元格经纬度边长（用于在前端把点扩成方格多边形）。
    """
    min_lon, min_lat, max_lon, max_lat = bbox
    lon_edges = np.linspace(min_lon, max_lon, n + 1)
    lat_edges = np.linspace(min_lat, max_lat, n + 1)
    lon_centers = (lon_edges[:-1] + lon_edges[1:]) / 2
    lat_centers = (lat_edges[:-1] + lat_edges[1:]) / 2
    grid_lon, grid_lat = np.meshgrid(lon_centers, lat_centers)
    dlon = lon_edges[1] - lon_edges[0]
    dlat = lat_edges[1] - lat_edges[0]
    return grid_lon.ravel(), grid_lat.ravel(), dlon, dlat
