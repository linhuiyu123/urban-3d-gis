"""
内置样例数据生成器（离线、确定性）。

当 data/<city>/ 下没有真实 GeoJSON 时，后端调用本模块按城市 bbox
程序化生成一套自洽的演示数据：六类 POI、连通路网、应急避难场所、水体。
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


# 更丰富的样例名称
POI_NAMES = {
    "scenic": ["西湖风景区", "灵隐寺", "雷峰塔", "钱塘江观潮点", "西溪湿地", "宋城",
               "龙井茶园", "植物园", "动物园", "运河文化广场", "湖滨公园", "太子湾公园"],
    "commercial": ["武林广场商圈", "湖滨银泰", "万象城", "龙湖天街", "城西银泰城",
                   "滨江宝龙城", "来福士中心", "西溪印象城", "大悦城", "万达广场",
                   "世纪联华", "盒马鲜生"],
    "school": ["浙江大学", "浙江工业大学", "杭州师范大学", "中国美术学院",
               "学军中学", "杭州外国语学校", "杭二中", "杭十四中",
               "西湖小学", "保俶塔实验学校", "胜利小学", "采荷中学"],
    "hospital": ["浙大附属第一医院", "浙大附属第二医院", "杭州市第一人民医院",
                 "浙江省人民医院", "邵逸夫医院", "省中医院", "市儿童医院"],
    "transit": ["火车东站", "城站", "凤起路站", "武林广场站", "龙翔桥站",
                "江陵路站", "钱江路站", "市民中心站", "西湖文化广场站",
                "定安路站", "近江站", "文泽路站", "客运中心站",
                "临平站", "滨康路站", "建设一路站", "下沙西站",
                "火车西站", "萧山国际机场", "汽车北站", "九堡客运中心"],
    "road": []  # 由 _arterial_points 填充
}

# 东京样例名称
TOKYO_NAMES = {
    "scenic": ["浅草寺", "东京塔", "皇居", "明治神宫", "上野公园",
               "台场", "天空树", "秋叶原", "筑地市场", "新宿御苑"],
    "commercial": ["银座", "新宿", "涩谷", "池袋", "六本木",
                   "表参道", "有乐町", "丸之内", "品川", "惠比寿"],
    "school": ["东京大学", "早稻田大学", "庆应义塾大学", "东京工业大学",
               "御茶水女子大学", "东京外国语大学", "筑波大学附属", "开成高校"],
    "hospital": ["东京大学医学部附属医院", "圣路加国际医院", "东京医科齿科大学医院",
                 "顺天堂医院", "虎之门医院", "国立国际医疗研究中心"],
    "transit": ["东京站", "新宿站", "涩谷站", "品川站", "上野站",
                "池袋站", "秋叶原站", "东京巨蛋站", "滨松町站", "有乐町站",
                "羽田机场", "成田机场", "锦糸町站", "两国站", "巢鸭站"],
    "road": []
}


def _pick_names(rng, pool, n):
    """从名称池中随机选取 n 个不重复的名称。池不够时自动编号补齐。"""
    names = list(pool)
    rng.shuffle(names)
    result = names[:n]
    missing = n - len(result)
    if missing > 0:
        base = "站点" if any(k in str(pool[0]) for k in ["站", "场", "园"]) else "设施"
        result += [f"{base}{len(result) + i + 1}" for i in range(missing)]
    return result


def generate_pois(city: str) -> dict:
    """生成六类 POI。商业/景点呈簇状，学校/医院散布，公交沿网格。"""
    rng = _rng(city)
    cfg = CITIES[city]
    bbox = cfg["bbox"]
    cx, cy = cfg["center"]
    is_tokyo = "tokyo" in city.lower()

    pts = {
        "scenic": _cluster(rng, cx - 0.012, cy - 0.025, 8, 0.006),
        "commercial": _cluster(rng, cx, cy, 10, 0.008) + _cluster(rng, cx + 0.03, cy + 0.02, 5, 0.006),
        "school": _scatter(rng, bbox, 13),
        "hospital": _scatter(rng, bbox, 7),
        "transit": _scatter(rng, bbox, 22, margin=0.05),
        "road": [],  # 下方用主干道采样填充
    }

    name_pool = TOKYO_NAMES if is_tokyo else POI_NAMES
    features = []
    for cat, coords in pts.items():
        names = _pick_names(rng, name_pool.get(cat, []), len(coords))
        for i, (lon, lat) in enumerate(coords):
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {"category": cat, "name": names[i] if i < len(names) else f"{cat}{i + 1}"},
            })
    return {"type": "FeatureCollection", "features": features}


def generate_roads(city: str, nx: int = 14, ny: int = 14) -> dict:
    """生成连通的近似网格路网，含主干道/次干道/支路，名称随机生成。"""
    rng = _rng(city + "_roads")
    bbox = CITIES[city]["bbox"]
    min_lon, min_lat, max_lon, max_lat = bbox
    min_lon, max_lon = min_lon + 0.02, max_lon - 0.02
    min_lat, max_lat = min_lat + 0.02, max_lat - 0.02
    is_tokyo = "tokyo" in city.lower()

    # 路名池
    road_names_cn = ["西湖大道", "延安路", "庆春路", "凤起路", "体育场路",
                     "环城北路", "莫干山路", "文三路", "文二路", "古墩路",
                     "天目山路", "钱江路", "江南大道", "江晖路", "滨康路",
                     "上塘路", "德胜路", "秋涛路", "解放路", "建国路"]
    road_names_jp = ["明治通", "昭和通", "青山通", "井之头通", "目黑通",
                     "环七通", "环八通", "外堀通", "内堀通", "早稻田通",
                     "新宿通", "甲州街道", "青梅街道", "目白通", "春日通"]

    road_pool = road_names_jp if is_tokyo else road_names_cn
    rng.shuffle(road_pool)

    nodes = {}
    for j in range(ny):
        for i in range(nx):
            lon = min_lon + (max_lon - min_lon) * i / (nx - 1) + rng.uniform(-0.0008, 0.0008)
            lat = min_lat + (max_lat - min_lat) * j / (ny - 1) + rng.uniform(-0.0008, 0.0008)
            nodes[(i, j)] = [round(lon, 6), round(lat, 6)]

    features = []
    name_idx = 0

    def add_edge(a, b, is_arterial, is_secondary):
        nonlocal name_idx
        if is_arterial:
            speed, kind = 60, "arterial"
        elif is_secondary:
            speed, kind = 45, "secondary"
        else:
            speed, kind = 30, "local"
        name = road_pool[name_idx % len(road_pool)] if road_pool else ""
        name_idx += 1
        features.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": [nodes[a], nodes[b]]},
            "properties": {"speed": speed, "kind": kind, "name": name},
        })

    for j in range(ny):
        for i in range(nx):
            is_arterial_row = (j % 4 == 0)
            is_secondary_row = (j % 2 == 0 and not is_arterial_row)
            is_arterial_col = (i % 4 == 0)
            is_secondary_col = (i % 2 == 0 and not is_arterial_col)
            if i + 1 < nx:
                add_edge((i, j), (i + 1, j), is_arterial_row, is_secondary_row)
            if j + 1 < ny:
                add_edge((i, j), (i, j + 1), is_arterial_col, is_secondary_col)

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
    rng = _rng(city + "_shelters")
    coords = _scatter(rng, CITIES[city]["bbox"], 7, margin=0.12)
    is_tokyo = "tokyo" in city.lower()
    shelter_names = (["东京巨蛋避难点", "上野公园避难所", "代代木公园", "日比谷公园",
                      "新宿中央公园", "驹泽奥林匹克公园", "有栖川宫纪念公园"]
                     if is_tokyo else
                     ["黄龙体育中心", "武林广场避难所", "西湖文化广场", "市民中心",
                      "奥体中心", "城北体育公园", "江干区体育馆"])
    rng.shuffle(shelter_names)
    feats = [{
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": c},
        "properties": {
            "name": shelter_names[i] if i < len(shelter_names) else f"应急避难场所{i + 1}",
            "capacity": rng.choice([2000, 5000, 8000]),
        },
    } for i, c in enumerate(coords)]
    return {"type": "FeatureCollection", "features": feats}


def generate_water(city: str) -> dict:
    """生成可信样例水系：近城区中心湖泊 + 弯曲河流 + 可选海岸线，供洪水从水体起淹。"""
    rng = _rng(city + "_water")
    min_lon, min_lat, max_lon, max_lat = CITIES[city]["bbox"]
    cx, cy = CITIES[city]["center"]
    w, h = max_lon - min_lon, max_lat - min_lat
    feats = []
    is_tokyo = "tokyo" in city.lower()

    # 1) 城市湖泊
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
    ring.append(ring[0])
    lake_name = "皇居护城河" if is_tokyo else "西湖"
    feats.append({"type": "Feature",
                  "geometry": {"type": "Polygon", "coordinates": [ring]},
                  "properties": {"kind": "lake", "name": lake_name}})

    # 2) 河流
    base = rng.uniform(0.25, 0.60)
    amp = rng.uniform(0.10, 0.24)
    phase = rng.uniform(0.0, 2 * math.pi)
    freq = rng.uniform(1.6, 2.6)
    n = 36
    center = []
    for i in range(n + 1):
        t = i / n
        lon = min_lon + 0.05 * w + 0.90 * w * t
        lat = min_lat + base * h + amp * h * math.sin(freq * math.pi * t + phase)
        center.append((lon, lat))
    rw = 0.012 * h
    left = [[round(lon, 6), round(lat + rw, 6)] for lon, lat in center]
    right = [[round(lon, 6), round(lat - rw, 6)] for lon, lat in reversed(center)]
    rring = left + right + [left[0]]
    river_name = "隅田川" if is_tokyo else "钱塘江"
    feats.append({"type": "Feature",
                  "geometry": {"type": "Polygon", "coordinates": [rring]},
                  "properties": {"kind": "river", "name": river_name}})

    # 3) 东京：添加海岸线
    if is_tokyo:
        coast = []
        npts = 24
        for i in range(npts + 1):
            t = i / npts
            clon = min_lon + 0.02 * w + 0.96 * w * t
            clat = min_lat + 0.04 * h + rng.uniform(-0.01, 0.01) * h
            coast.append([round(clon, 6), round(clat, 6)])
        feats.append({"type": "Feature",
                      "geometry": {"type": "LineString", "coordinates": coast},
                      "properties": {"kind": "coastline", "name": "东京湾海岸线"}})

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
