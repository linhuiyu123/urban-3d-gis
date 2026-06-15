"""
数据加载层。

负责把 data/ 下的 GeoJSON（POI、路网、避难场所等）读入内存，
并按城市 / POI 类别建立索引，供各分析引擎调用。

为了避免每次请求都重新读盘，这里做了简单的内存缓存。
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from .config import DATA_DIR
from . import sample_data

# POI 的六大类别（与价值评估、AI 助手中的语义一一对应）
POI_CATEGORIES = ["scenic", "commercial", "school", "hospital", "transit", "road"]

# 类别中文名，便于前端展示与 AI 解析
POI_CN = {
    "scenic": "旅游景点",
    "commercial": "商业区",
    "school": "学校",
    "hospital": "医院",
    "transit": "公共交通",
    "road": "主干道",
}


def _read_geojson(path: Path) -> dict[str, Any]:
    """读取单个 GeoJSON 文件；文件不存在时返回空 FeatureCollection。"""
    if not path.exists():
        return {"type": "FeatureCollection", "features": []}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_or_generate(city: str, fname: str, kind: str) -> dict[str, Any]:
    """
    优先读取磁盘上的真实 GeoJSON；若文件不存在**或为空**，则调用样例数据生成器
    程序化生成（离线开箱即用）。kind ∈ {pois, roads, shelters, water}。

    关键：磁盘文件“存在但 0 要素”也要回退样例。常见于抓取脚本在 Overpass 被限流/失败时
    写入了空的 FeatureCollection（如国内访问失败）——若仍直接采用，会导致 POI/水系整层为空，
    连带价值评估全 0、选址 0 个、热点全不显著、洪水退化成无水体的高程 blob。
    """
    path = DATA_DIR / city / fname
    if path.exists():
        fc = _read_geojson(path)
        if fc.get("features"):                 # 仅当非空才使用磁盘数据
            return fc
    return sample_data.generate_all(city)[kind]


@lru_cache(maxsize=8)
def load_pois(city: str) -> dict[str, Any]:
    """加载某城市的全部 POI（一个 FeatureCollection，properties.category 标明类别）。"""
    return _load_or_generate(city, "pois.geojson", "pois")


@lru_cache(maxsize=8)
def load_roads(city: str) -> dict[str, Any]:
    """加载道路网（LineString FeatureCollection，含 properties.speed 行驶速度 km/h）。"""
    return _load_or_generate(city, "roads.geojson", "roads")


@lru_cache(maxsize=8)
def load_shelters(city: str) -> dict[str, Any]:
    """加载应急避难场所（Point FeatureCollection）。"""
    return _load_or_generate(city, "shelters.geojson", "shelters")


@lru_cache(maxsize=8)
def load_water(city: str) -> dict[str, Any]:
    """加载水系（河/湖/海岸线，用于洪水从水体起淹）。"""
    return _load_or_generate(city, "water.geojson", "water")


def pois_by_category(city: str, category: str) -> list[dict[str, Any]]:
    """取出某城市指定类别的 POI 要素列表。"""
    fc = load_pois(city)
    return [f for f in fc.get("features", []) if f["properties"].get("category") == category]


def clear_cache() -> None:
    """数据更新后调用，清空缓存。"""
    load_pois.cache_clear()
    load_roads.cache_clear()
    load_shelters.cache_clear()
    load_water.cache_clear()
