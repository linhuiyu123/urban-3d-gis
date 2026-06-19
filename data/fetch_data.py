"""
联网抓取真实 OSM 数据，覆盖内置样例。

抓取内容（按研究区 bbox）：
  - 路网 roads.geojson    ：主干+次干道（OSMnx，控制规模以适配大范围）
  - 分类 POI pois.geojson  ：景点/商业/学校/医院/公交（Overpass，按类别限量）
  - 水系 water.geojson     ：河流/湖泊/海岸线（Overpass，供洪水模拟从水体起淹）

用法：
    cd data
    python fetch_data.py                 # 抓取全部研究区
    python fetch_data.py hangzhou_core   # 仅抓某区域
    python fetch_data.py --water-only hangzhou_metro
    python fetch_data.py --only water hangzhou_core hangzhou_metro

说明：范围越大抓取越慢；杭州全域(hangzhou_full)数据量大，建议优先 core/metro。
国内访问 Overpass/OSM 可能较慢或需代理；失败时后端自动回退内置样例，不影响启动。
"""
import argparse
import json
import sys
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
try:
    from app.config import AREAS, DATA_DIR     # noqa: E402
except ModuleNotFoundError as exc:
    if exc.name != "pydantic_settings":
        raise

    # fetch_data.py only needs the static AREAS/DATA_DIR values from config.py.
    # Allow data fetching on machines that did not install the full backend runtime.
    pydantic_settings = types.ModuleType("pydantic_settings")

    class BaseSettings:
        pass

    def SettingsConfigDict(**kwargs):
        return kwargs

    pydantic_settings.BaseSettings = BaseSettings
    pydantic_settings.SettingsConfigDict = SettingsConfigDict
    sys.modules["pydantic_settings"] = pydantic_settings
    from app.config import AREAS, DATA_DIR     # noqa: E402

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
HEADERS = {
    "User-Agent": "urban-3d-gis-course-project/1.0 (student data fetch)",
}

POI_QUERIES = {
    "scenic": 'node["tourism"~"attraction|museum|viewpoint|theme_park"]',
    "commercial": 'node["shop"~"mall|supermarket|department_store"];node["amenity"="marketplace"]',
    "school": 'node["amenity"~"school|university|college"]',
    "hospital": 'node["amenity"="hospital"]',
    "transit": 'node["railway"="station"];node["station"="subway"]',
}
POI_CAP = 1500   # 每类最多保留，避免大范围下要素爆炸


def _overpass(query: str, retries: int = 2):
    import requests
    for i in range(retries + 1):
        try:
            r = requests.post(OVERPASS_URL, data={"data": query}, headers=HEADERS, timeout=180)
            r.raise_for_status()
            return r.json()
        except Exception as ex:
            print(f"      Overpass 重试 {i+1}: {ex}")
            time.sleep(3)
    return {"elements": []}


def fetch_pois(bbox):
    s, w, n, e = bbox[1], bbox[0], bbox[3], bbox[2]
    feats = []
    for category, selectors in POI_QUERIES.items():
        body = "".join(f"{sel}({s},{w},{n},{e});" for sel in selectors.split(";") if sel)
        q = f"[out:json][timeout:120];({body});out center {POI_CAP};"
        data = _overpass(q)
        cnt = 0
        for el in data.get("elements", []):
            lon = el.get("lon") or el.get("center", {}).get("lon")
            lat = el.get("lat") or el.get("center", {}).get("lat")
            if lon is None:
                continue
            feats.append({"type": "Feature",
                          "geometry": {"type": "Point", "coordinates": [lon, lat]},
                          "properties": {"category": category,
                                         "name": el.get("tags", {}).get("name", category)}})
            cnt += 1
        print(f"    {category}: {cnt}")
    return {"type": "FeatureCollection", "features": feats}


def fetch_water(bbox):
    """抓取水体：湖/水库面、河流面、海岸线。供洪水从水体起淹。"""
    s, w, n, e = bbox[1], bbox[0], bbox[3], bbox[2]
    q = (f'[out:json][timeout:180];('
         f'way["natural"="water"]({s},{w},{n},{e});'
         f'relation["natural"="water"]({s},{w},{n},{e});'
         f'way["waterway"="riverbank"]({s},{w},{n},{e});'
         f'way["natural"="coastline"]({s},{w},{n},{e});'
         f');out geom;')
    data = _overpass(q)
    feats = []
    for el in data.get("elements", []):
        geom = el.get("geometry")
        if not geom:
            continue
        coords = [[p["lon"], p["lat"]] for p in geom]
        if len(coords) < 2:
            continue
        is_coast = el.get("tags", {}).get("natural") == "coastline"
        if not is_coast and coords[0] != coords[-1]:
            coords.append(coords[0])  # 闭合成面
        gtype = "LineString" if is_coast else "Polygon"
        geometry = {"type": "LineString", "coordinates": coords} if is_coast \
            else {"type": "Polygon", "coordinates": [coords]}
        feats.append({"type": "Feature", "geometry": geometry,
                      "properties": {"kind": el.get("tags", {}).get("natural", "water")}})
    print(f"    water: {len(feats)} 个水体")
    return {"type": "FeatureCollection", "features": feats}


def fetch_roads(bbox, big: bool):
    """抓取路网。优先用 OSMnx；缺少依赖时退回 Overpass way 查询。

    大范围只取主干/次干道以控制规模。Overpass fallback 不做拓扑简化，
    但会输出与后端 routing.build_graph 兼容的 LineString GeoJSON。
    """
    try:
        import osmnx as ox
    except ImportError:
        return fetch_roads_overpass(bbox, big)

    if big:
        cf = '["highway"~"motorway|trunk|primary|secondary"]'
    else:
        cf = '["highway"~"motorway|trunk|primary|secondary|tertiary|residential|unclassified"]'
    G = ox.graph_from_bbox(bbox[3], bbox[1], bbox[2], bbox[0],
                           network_type="drive", custom_filter=cf, simplify=True)
    feats = []
    for u, v, d in G.edges(data=True):
        geom = d.get("geometry")
        coords = ([[x, y] for x, y in geom.coords] if geom
                  else [[G.nodes[u]["x"], G.nodes[u]["y"]], [G.nodes[v]["x"], G.nodes[v]["y"]]])
        hwy = d.get("highway")
        hwy = hwy[0] if isinstance(hwy, list) else hwy
        speed = {"motorway": 90, "trunk": 70, "primary": 60, "secondary": 50,
                 "tertiary": 40, "residential": 30, "unclassified": 30}.get(hwy, 35)
        feats.append({"type": "Feature",
                      "geometry": {"type": "LineString", "coordinates": coords},
                      "properties": {"speed": speed, "kind": hwy}})
    print(f"    roads: {len(feats)} 段")
    return {"type": "FeatureCollection", "features": feats}


def fetch_roads_overpass(bbox, big: bool):
    """不依赖 OSMnx 的道路抓取 fallback。"""
    s, w, n, e = bbox[1], bbox[0], bbox[3], bbox[2]
    hwy_re = "motorway|trunk|primary|secondary" if big \
        else "motorway|trunk|primary|secondary|tertiary|residential|unclassified"
    q = (f'[out:json][timeout:180];('
         f'way["highway"~"{hwy_re}"]({s},{w},{n},{e});'
         f');out geom;')
    data = _overpass(q)
    feats = []
    for el in data.get("elements", []):
        geom = el.get("geometry")
        if not geom or len(geom) < 2:
            continue
        coords = [[p["lon"], p["lat"]] for p in geom]
        hwy = el.get("tags", {}).get("highway")
        hwy = hwy[0] if isinstance(hwy, list) else hwy
        speed = {"motorway": 90, "trunk": 70, "primary": 60, "secondary": 50,
                 "tertiary": 40, "residential": 30, "unclassified": 30}.get(hwy, 35)
        feats.append({"type": "Feature",
                      "geometry": {"type": "LineString", "coordinates": coords},
                      "properties": {"speed": speed, "kind": hwy, "source": "overpass"}})
    print(f"    roads: {len(feats)} 段（Overpass fallback）")
    return {"type": "FeatureCollection", "features": feats}


FETCHERS = {
    "pois": lambda cfg, big: fetch_pois(cfg["bbox"]),
    "water": lambda cfg, big: fetch_water(cfg["bbox"]),
    "roads": lambda cfg, big: fetch_roads(cfg["bbox"], big),
}


def main(areas, kinds=None):
    kinds = kinds or ["pois", "water", "roads"]
    for area in areas:
        cfg = AREAS[area]
        big = (cfg["bbox"][2] - cfg["bbox"][0]) > 0.6   # 经度跨度大于0.6°视为大范围
        print(f"[{cfg['name']}] 抓取中（big={big}, kinds={','.join(kinds)}）…")
        out = DATA_DIR / area
        out.mkdir(parents=True, exist_ok=True)
        for fn in kinds:
            try:
                fc = FETCHERS[fn](cfg, big)
                n = len(fc.get("features", []))
                if n == 0:
                    # 关键：抓到 0 要素绝不落盘。否则空文件会覆盖内置样例，
                    # 导致 POI/水系整层为空（价值全 0、选址 0 个、热点全不显著、洪水退化）。
                    print(f"    [跳过写入 {fn}] 抓到 0 要素（Overpass 可能被限流/被墙/超时）；"
                          f"保留内置样例。建议配置代理后重试。")
                    continue
                (out / f"{fn}.geojson").write_text(json.dumps(fc, ensure_ascii=False),
                                                   encoding="utf-8")
                print(f"    [写入 {fn}] {n} 要素")
            except Exception as ex:
                print(f"    [跳过 {fn}] {ex}")
        print(f"[{cfg['name']}] 完成。")


def parse_args():
    parser = argparse.ArgumentParser(description="Fetch OSM data for project areas.")
    parser.add_argument(
        "areas",
        nargs="*",
        choices=list(AREAS.keys()),
        help="研究区 id；不填则抓取全部研究区。",
    )
    parser.add_argument(
        "--only",
        nargs="+",
        choices=list(FETCHERS.keys()),
        metavar="KIND",
        help="只抓指定数据层，可选：pois water roads。例如：--only water",
    )
    parser.add_argument(
        "--water-only",
        action="store_true",
        help="等价于 --only water。",
    )
    args = parser.parse_args()
    if args.water_only:
        args.only = ["water"]
    return args


if __name__ == "__main__":
    args = parse_args()
    targets = args.areas or list(AREAS.keys())
    main(targets, args.only)
