"""
住房 / 地段价值评估引擎。

核心思想（缓冲区 + 距离衰减 + 加权叠加）：
  1. 在研究区生成规则网格；
  2. 对每个网格中心，计算到六类 POI（景点/商业/学校/医院/公交/主干道）
     最近设施的距离（米，UTM 平面）；
  3. 用指数距离衰减函数把"距离"换算成 0~1 的"邻近度得分"——
     越近得分越高，超出该类别的影响半径后趋近于 0；
  4. 按各类别权重加权求和，归一化到 0~100，即综合地段价值。

选址分析即此结果的反向查询：给定权重与阈值，筛出高分网格。

距离衰减公式： s = exp(-d / decay)
  其中 decay 为该类别的特征影响距离（半衰减尺度），可理解为缓冲区软边界。
"""
from __future__ import annotations

import math

import numpy as np
from scipy.spatial import cKDTree

from ..config import CITIES
from ..data_loader import POI_CATEGORIES, pois_by_category
from . import geoutils

# 每类设施的默认权重与影响距离（米）。权重之和不必为 1，最终会归一化。
DEFAULT_WEIGHTS = {
    "scenic": 0.15,      # 旅游景点
    "commercial": 0.25,  # 商业区（对地段价值影响最大）
    "school": 0.20,      # 学校（学区）
    "hospital": 0.15,    # 医院
    "transit": 0.20,     # 公共交通
    "road": 0.05,        # 主干道（太近反而有噪声，权重低）
}
DECAY = {
    "scenic": 1500,
    "commercial": 800,
    "school": 600,
    "hospital": 1000,
    "transit": 500,
    "road": 400,
}


def _area_decay_scale(bbox: list[float]) -> float:
    """衰减尺度随研究区尺寸自适应。

    DECAY 里的基准值（数百米）是“街区级”尺度；当研究区跨数十甚至上百公里时，
    若仍用基准值，几乎每个网格都远离所有设施 → 邻近度≈0 → 价值面整片“全蓝”、
    选址也几乎筛不出地块。这里按研究区对角线长度放大衰减尺度，使价值在城市尺度上
    有合理梯度（杭州主城≈×6、东京≈×3、杭州全域≈×30）。
    """
    min_lon, min_lat, max_lon, max_lat = bbox
    mid_lat = (min_lat + max_lat) / 2
    w_km = (max_lon - min_lon) * 111.0 * math.cos(math.radians(mid_lat))
    h_km = (max_lat - min_lat) * 111.0
    diag_km = math.hypot(w_km, h_km)
    return max(2.0, diag_km / 8.0)


def _nearest_distances(city: str, gx: np.ndarray, gy: np.ndarray, category: str) -> np.ndarray:
    """计算每个网格点到指定类别最近 POI 的距离（米）。无该类 POI 时返回较大距离。"""
    feats = pois_by_category(city, category)
    coords = geoutils.coords_array(feats)
    if len(coords) == 0:
        return np.full(gx.shape, 1e9)
    px, py = geoutils.lonlat_to_utm(city, coords[:, 0], coords[:, 1])
    tree = cKDTree(np.column_stack([px, py]))
    dist, _ = tree.query(np.column_stack([gx, gy]), k=1)
    return dist


def assess_value(city: str, weights: dict[str, float] | None = None,
                 resolution: int = 48) -> dict:
    """
    计算研究区价值评估网格。

    返回 GeoJSON FeatureCollection，每个要素是一个方格 polygon，
    properties.score 为 0~100 的综合价值分，并附各类别的分项得分，便于解释。
    """
    weights = {**DEFAULT_WEIGHTS, **(weights or {})}
    bbox = CITIES[city]["bbox"]
    decay_scale = _area_decay_scale(bbox)        # 衰减尺度随研究区大小自适应

    lons, lats, dlon, dlat = geoutils.bbox_grid(bbox, resolution)
    gx, gy = geoutils.lonlat_to_utm(city, lons, lats)
    gx, gy = np.asarray(gx), np.asarray(gy)

    total = np.zeros(lons.shape)
    breakdown: dict[str, np.ndarray] = {}
    wsum = sum(abs(w) for w in weights.values()) or 1.0

    for cat in POI_CATEGORIES:
        d = _nearest_distances(city, gx, gy, cat)
        proximity = np.exp(-d / (DECAY[cat] * decay_scale))   # 0~1，越近越高
        breakdown[cat] = proximity
        total += weights.get(cat, 0.0) * proximity

    # 归一化到 0~100：2/98 分位裁剪后线性拉伸。
    # 注：之前用过“按秩归一化”，但当 POI 整层缺失（抓取失败写了空文件）导致邻近度全为 0 时，
    # 大量平局会按网格顺序排出一条假梯度——就是看到的“彩虹旗”，且与权重无关。改回基于数值的
    # 归一化后：有数据则呈现真实的、随权重变化的价值梯度；真为退化分布时也只是整体偏低而非彩虹。
    raw = total / wsum
    lo, hi = np.percentile(raw, 2), np.percentile(raw, 98)
    if hi - lo < 1e-9:                                 # 退化分布兜底，避免除零放大噪声
        lo, hi = float(raw.min()), float(raw.max())
    score = np.clip((raw - lo) / (hi - lo + 1e-9), 0.0, 1.0) * 100.0

    features = []
    for i in range(len(lons)):
        lon, lat = float(lons[i]), float(lats[i])
        half_lon, half_lat = dlon / 2, dlat / 2
        ring = [
            [lon - half_lon, lat - half_lat],
            [lon + half_lon, lat - half_lat],
            [lon + half_lon, lat + half_lat],
            [lon - half_lon, lat + half_lat],
            [lon - half_lon, lat - half_lat],
        ]
        features.append({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [ring]},
            "properties": {
                "score": round(float(score[i]), 1),
                "lon": lon, "lat": lat,
                "detail": {c: round(float(breakdown[c][i]), 3) for c in POI_CATEGORIES},
            },
        })

    return {
        "type": "FeatureCollection",
        "features": features,
        "meta": {"city": city, "resolution": resolution, "weights": weights,
                 "score_mean": round(float(score.mean()), 1),
                 "score_max": round(float(score.max()), 1),
                 "score_p90": round(float(np.percentile(score, 90)), 1),
                 "decay_scale": round(decay_scale, 1)},
    }


def site_selection(city: str, min_score: float = 70.0,
                   weights: dict[str, float] | None = None,
                   resolution: int = 48, top_k: int | None = None) -> dict:
    """
    选址分析：在价值评估基础上筛选高分地块。

    - min_score：分数阈值，仅保留 score >= 阈值 的网格；
    - top_k：若指定，仅返回分数最高的 K 个地块。
    """
    grid = assess_value(city, weights, resolution)
    feats = [f for f in grid["features"] if f["properties"]["score"] >= min_score]
    feats.sort(key=lambda f: f["properties"]["score"], reverse=True)
    n_qualified = len(feats)
    if top_k:
        feats = feats[:top_k]
    return {
        "type": "FeatureCollection",
        "features": feats,
        "meta": {"city": city, "min_score": min_score, "count": len(feats),
                 "qualified": n_qualified,           # 达标地块总数（top_k 截断前）
                 "top_score": round(feats[0]["properties"]["score"], 1) if feats else 0,
                 "resolution": resolution},
    }
