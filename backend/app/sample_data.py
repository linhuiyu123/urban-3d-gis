"""
内置样例数据生成器（离线、确定性）。

当 data/<city>/ 下没有真实 GeoJSON 时，后端调用本模块按城市 bbox
程序化生成一套自洽的演示数据：六类 POI、连通路网、应急避难场所。
使用固定随机种子，保证每次生成结果一致（便于复现与讲解）。

真实数据可用 data/fetch_data.py 抓取后覆盖，无需改动任何代码。
"""
from __future__ import annotations

import hashlib
import math
import random

from .config import CITIES


def _rng(city: str) -> random.Random:
    """每个城市一个固定种子的随机源。用 md5 而非内置 hash，保证跨进程可复现。"""
    seed = int(hashlib.md5(city.encode("utf-8")).hexdigest(), 16) & 0xFFFFFFFF
    return random.Random(seed)


def _cluster(rng, cx, cy, n, spread):
    """在 (cx,cy) 周围生成 n 个高斯散布的点。"""
    return [[round(cx + rng.gauss(0, spread), 6), round(cy + rng.gauss(0, spread), 6)]
            for _ in range(n)]


def _scatter(rng, bbox, n, margin=0.1):
    """在 bbox 内（留边）均匀散布 n 个点。"""
    min_lon, min_lat, max_lon, max_lat = bbox
    w, h = max_lon - min_lon, max_lat - min_lat
    return [[round(rng.uniform(min_lon + margin * w, max_lon - margin * w), 6),
             round(rng.uniform(min_lat + margin * h, max_lat - margin * h), 6)]
            for _ in range(n)]


def generate_pois(city: str) -> dict:
    """生成六类 POI。商业/景点呈簇状，学校/医院散布，公交沿网格。"""
    rng = _rng(city)
    cfg = CITIES[city]
    bbox = cfg["bbox"]
    cx, cy = cfg["center"]

    pts = {
        "scenic": _cluster(rng, cx - 0.012, cy - 0.025, 7, 0.006),
        "commercial": _cluster(rng, cx, cy, 9, 0.008) + _cluster(rng, cx + 0.03, cy + 0.02, 5, 0.006),
        "school": _scatter(rng, bbox, 13),
        "hospital": _scatter(rng, bbox, 6),
        "transit": _scatter(rng, bbox, 22, margin=0.05),
        "road": _cluster(rng, cx, cy, 0, 0),  # 占位，下方用主干道采样填充
    }

    names = {"scenic": "景点", "commercial": "商圈", "school": "学校",
             "hospital": "医院", "transit": "站点", "road": "主干道"}
    features = []
    for cat, coords in pts.items():
        for i, (lon, lat) in enumerate(coords):
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {"category": cat, "name": f"{names[cat]}{i + 1}"},
            })
    return {"type": "FeatureCollection", "features": features}


def generate_roads(city: str, nx: int = 14, ny: int = 14) -> dict:
    """生成连通的近似网格路网；每隔几条设为主干道（高限速）。"""
    rng = _rng(city)
    bbox = CITIES[city]["bbox"]
    min_lon, min_lat, max_lon, max_lat = bbox
    # 留边
    min_lon, max_lon = min_lon + 0.02, max_lon - 0.02
    min_lat, max_lat = min_lat + 0.02, max_lat - 0.02

    # 生成带轻微抖动的格点
    nodes = {}
    for j in range(ny):
        for i in range(nx):
            lon = min_lon + (max_lon - min_lon) * i / (nx - 1) + rng.uniform(-0.0008, 0.0008)
            lat = min_lat + (max_lat - min_lat) * j / (ny - 1) + rng.uniform(-0.0008, 0.0008)
            nodes[(i, j)] = [round(lon, 6), round(lat, 6)]

    features = []

    def add_edge(a, b, arterial):
        speed = 60 if arterial else 40
        features.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": [nodes[a], nodes[b]]},
            "properties": {"speed": speed, "kind": "arterial" if arterial else "local"},
        })

    for j in range(ny):
        for i in range(nx):
            arterial_row = (j % 4 == 0)
            arterial_col = (i % 4 == 0)
            if i + 1 < nx:
                add_edge((i, j), (i + 1, j), arterial_row)
            if j + 1 < ny:
                add_edge((i, j), (i, j + 1), arterial_col)

    return {"type": "FeatureCollection", "features": features}


def _arterial_points(roads: dict, every: int = 3) -> list:
    """从主干道上采样若干点，作为 'road' 类 POI（用于价值评估的临街邻近度）。"""
    pts = []
    arterials = [f for f in roads["features"] if f["properties"].get("kind") == "arterial"]
    for k, f in enumerate(arterials):
        if k % every == 0:
            c = f["geometry"]["coordinates"]
            mid = [round((c[0][0] + c[1][0]) / 2, 6), round((c[0][1] + c[1][1]) / 2, 6)]
            pts.append(mid)
    return pts


def generate_shelters(city: str) -> dict:
    """生成应急避难场所（公园 / 体育场 / 学校操场等）。"""
    rng = _rng(city)
    coords = _scatter(rng, CITIES[city]["bbox"], 7, margin=0.12)
    feats = [{
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": c},
        "properties": {"name": f"应急避难场所{i + 1}", "capacity": rng.choice([2000, 5000, 8000])},
    } for i, c in enumerate(coords)]
    return {"type": "FeatureCollection", "features": feats}


def generate_water(city: str) -> dict:
    """生成更可信的样例水系：近城区中心的湖泊 + 一条带弯的河流，供洪水从水体起淹。

    旧版只有一条 bbox 对角直线河，淹没看起来位置很假；这里改成“湖 + 河”、并按研究区
    尺寸自适应定位与缩放，使洪水从水体向外扩散时形态更自然。
    真实水系可用 data/fetch_data.py 抓取后覆盖（生成 data/<区域>/water.geojson）。
    """
    rng = _rng(city + "_water")
    min_lon, min_lat, max_lon, max_lat = CITIES[city]["bbox"]
    cx, cy = CITIES[city]["center"]
    w, h = max_lon - min_lon, max_lat - min_lat
    feats = []

    # 1) 城市湖泊（位置/大小随城市变化，避免各研究区水系长得一模一样）
    lake_cx = cx + rng.uniform(-0.12, 0.06) * w
    lake_cy = cy + rng.uniform(-0.10, 0.06) * h
    rx = (0.05 + rng.uniform(0.0, 0.04)) * w
    ry = (0.045 + rng.uniform(0.0, 0.035)) * h
    m = 40
    ring = []
    for i in range(m):
        a = 2 * math.pi * i / m
        r = 1.0 + 0.12 * math.sin(3 * a) + rng.uniform(-0.05, 0.05)
        ring.append([round(lake_cx + rx * r * math.cos(a), 6),
                     round(lake_cy + ry * r * math.sin(a), 6)])
    ring.append(ring[0])                  # 闭合外环（GeoJSON 要求首尾点相同）
    feats.append({"type": "Feature",
                  "geometry": {"type": "Polygon", "coordinates": [ring]},
                  "properties": {"kind": "lake", "name": "城市湖泊"}})

    # 2) 河流：横贯研究区、带正弦弯曲；基准位置/弯曲幅度/相位随城市变化
    base = rng.uniform(0.25, 0.60)       # 河流南北基准位置
    amp = rng.uniform(0.12, 0.26)        # 弯曲幅度
    phase = rng.uniform(0.0, 2 * math.pi)
    freq = rng.uniform(1.6, 2.6)
    n = 36
    center = []
    for i in range(n + 1):
        t = i / n
        lon = min_lon + 0.05 * w + 0.90 * w * t
        lat = min_lat + base * h + amp * h * math.sin(freq * math.pi * t + phase)
        center.append((lon, lat))
    rw = 0.012 * h                       # 河宽（纬度方向半宽）
    left = [[round(lon, 6), round(lat + rw, 6)] for lon, lat in center]
    right = [[round(lon, 6), round(lat - rw, 6)] for lon, lat in reversed(center)]
    rring = left + right + [left[0]]
    feats.append({"type": "Feature",
                  "geometry": {"type": "Polygon", "coordinates": [rring]},
                  "properties": {"kind": "river", "name": "样例河流"}})

    return {"type": "FeatureCollection", "features": feats}


def generate_all(city: str) -> dict[str, dict]:
    """一次生成某城市的全部样例数据，并把主干道采样点补进 'road' 类 POI。"""
    pois = generate_pois(city)
    roads = generate_roads(city)
    for i, mid in enumerate(_arterial_points(roads)):
        pois["features"].append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": mid},
            "properties": {"category": "road", "name": f"主干道采样{i + 1}"},
        })
    shelters = generate_shelters(city)
    water = generate_water(city)
    return {"pois": pois, "roads": roads, "shelters": shelters, "water": water}
