"""
数据加载层。

负责把 data/ 下的 GeoJSON（POI、路网、避难场所、水体）读入内存，
并按城市 / POI 类别建立索引，供各分析引擎调用。

为避免每次请求都重新读盘，做了内存缓存。
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from .config import DATA_DIR, CITIES
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

    磁盘文件"存在但 0 要素"也要回退样例。常见场景：抓取脚本受限流/失败
    写入空 FeatureCollection——若直接采用，会导致整层为空。
    """
    path = DATA_DIR / city / fname
    if path.exists():
        fc = _read_geojson(path)
        if fc.get("features"):
            return fc
    return sample_data.generate_all(city)[kind]


@lru_cache(maxsize=8)
def load_pois(city: str) -> dict[str, Any]:
    """加载某城市的全部 POI（FeatureCollection，properties.category 标明类别）。"""
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


def get_data_stats(city: str) -> dict:
    """
    返回某城市当前数据统计摘要（便于前端调试/监控）。
    包含各层要素数、数据来源（真实/样例）、POI 分类计数。
    """
    stats = {
        "city": city,
        "city_name": CITIES.get(city, {}).get("name", city),
        "layers": {},
    }

    for name, loader in [("pois", load_pois), ("roads", load_roads),
                          ("shelters", load_shelters), ("water", load_water)]:
        fc = loader(city)
        features = fc.get("features", [])
        # 判断数据来源（是否从真实文件读取）
        path = DATA_DIR / city / f"{name}.geojson"
        source = "disk" if (path.exists() and _read_geojson(path).get("features")) else "sample"
        layer_info = {
            "count": len(features),
            "source": source,
        }
        if name == "pois":
            # POI 分类计数
            cats = {}
            for f in features:
                cat = f["properties"].get("category", "unknown")
                cats[cat] = cats.get(cat, 0) + 1
            layer_info["categories"] = cats
        if name == "roads":
            kinds = {}
            for f in features:
                kind = f["properties"].get("kind", "unknown")
                kinds[kind] = kinds.get(kind, 0) + 1
            layer_info["road_types"] = kinds
        stats["layers"][name] = layer_info

    return stats
