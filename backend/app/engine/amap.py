"""高德(AMap) 在线路径规划代理 + WGS84<->GCJ-02 坐标转换。

为什么要在后端代理：
  1) 不能把高德 Key 暴露到前端（前端代码任何人可见）；
  2) 高德返回 GCJ-02「火星坐标」，而本平台 Cesium 地图是 WGS84——不转换路线会偏移几百米。

用高德 **REST v5** 接口：驾车加 alternative_route=3 可一次返回最多 3 条备选（与高德网页版/App 一致，
含"紫之隧道"等大路方案）。配置：backend/.env 里  AMAP_KEY=你的高德Web服务Key （勿提交，已 gitignore）。
返回格式与自研 routing.route 一致（GeoJSON FeatureCollection），前端 renderRoute 可直接复用。
"""
from __future__ import annotations

import math
import os
from pathlib import Path

# ---------------- 坐标转换 WGS84 <-> GCJ-02 ----------------
_A = 6378245.0
_EE = 0.00669342162296594323


def _out_of_china(lon: float, lat: float) -> bool:
    return not (73.66 < lon < 135.05 and 3.86 < lat < 53.55)


def _tf_lat(x: float, y: float) -> float:
    ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(y * math.pi) + 40.0 * math.sin(y / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (160.0 * math.sin(y / 12.0 * math.pi) + 320 * math.sin(y * math.pi / 30.0)) * 2.0 / 3.0
    return ret


def _tf_lon(x: float, y: float) -> float:
    ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(x * math.pi) + 40.0 * math.sin(x / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (150.0 * math.sin(x / 12.0 * math.pi) + 300.0 * math.sin(x / 30.0 * math.pi)) * 2.0 / 3.0
    return ret


def wgs84_to_gcj02(lon: float, lat: float) -> tuple[float, float]:
    if _out_of_china(lon, lat):
        return lon, lat
    dlat = _tf_lat(lon - 105.0, lat - 35.0)
    dlon = _tf_lon(lon - 105.0, lat - 35.0)
    radlat = lat / 180.0 * math.pi
    magic = math.sin(radlat)
    magic = 1 - _EE * magic * magic
    sqrtmagic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((_A * (1 - _EE)) / (magic * sqrtmagic) * math.pi)
    dlon = (dlon * 180.0) / (_A / sqrtmagic * math.cos(radlat) * math.pi)
    return lon + dlon, lat + dlat


def gcj02_to_wgs84(lon: float, lat: float) -> tuple[float, float]:
    """近似反算（一次迭代，误差约 1~2 米，足够地图显示）。"""
    if _out_of_china(lon, lat):
        return lon, lat
    glon, glat = wgs84_to_gcj02(lon, lat)
    return lon * 2 - glon, lat * 2 - glat


# ---------------- 高德 v5 路径规划代理 ----------------
_ENDPOINT = {
    "drive": "https://restapi.amap.com/v5/direction/driving",
    "walk": "https://restapi.amap.com/v5/direction/walking",
    "cycle": "https://restapi.amap.com/v5/direction/bicycling",
}
MODE_CN = {"drive": "驾车", "walk": "步行", "cycle": "骑行", "transit": "公交"}


def _amap_key() -> str:
    """优先读环境变量 AMAP_KEY；否则从 backend/.env 读取（与 DEEPSEEK_API_KEY 同处）。"""
    k = os.getenv("AMAP_KEY")
    if k:
        return k.strip()
    env = Path(__file__).resolve().parent.parent.parent / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith("AMAP_KEY="):
                return s.split("=", 1)[1].strip()
    return ""


def _poly_str(v) -> str:
    """取出折线字符串：v5 有时把 polyline 包成 {"polyline": "..."}，也可能直接是字符串。"""
    if isinstance(v, dict):
        return v.get("polyline", "") or ""
    return v or ""


def _gcj_polyline_to_wgs84(s) -> list[list[float]]:
    """把高德 "lon,lat;lon,lat"(GCJ-02) 折线转成 [[lon,lat],...](WGS84)；兼容 dict 包裹与脏数据。"""
    pts: list[list[float]] = []
    for pair in _poly_str(s).split(";"):
        if not pair or "," not in pair:
            continue
        lon_s, lat_s = pair.split(",")[:2]
        try:
            w_lon, w_lat = gcj02_to_wgs84(float(lon_s), float(lat_s))
        except ValueError:
            continue
        pts.append([round(w_lon, 6), round(w_lat, 6)])
    return pts


def _parse_paths(data: dict, mode: str):
    """解析 v5 响应所有候选：返回 ([(dist_m, dur_s, polyline_str), ...], None) 或 (None, 错误信息)。"""
    if str(data.get("status")) != "1":
        return None, data.get("info") or data.get("errmsg") or "高德返回异常"
    paths = ((data.get("route") or {}).get("paths")) or []
    if not paths:
        return None, "未返回可用路线"
    out = []
    for p in paths:
        dist = float(p.get("distance", 0) or 0)
        cost = p.get("cost") or {}
        dur = float(cost.get("duration", 0) or p.get("duration", 0) or 0)   # v5 时长在 cost 里
        line = ";".join(_poly_str(s.get("polyline")) for s in p.get("steps", []) if _poly_str(s.get("polyline")))
        if line:
            out.append((dist, dur, line))
    if not out:
        return None, "高德返回的路线缺少 polyline"
    return out, None


def _loc_to_wgs(s: str) -> list[float]:
    """高德 "lon,lat"(GCJ-02) 单点转 WGS84 [lon,lat]。"""
    lon_s, lat_s = s.split(",")
    w = gcj02_to_wgs84(float(lon_s), float(lat_s))
    return [round(w[0], 6), round(w[1], 6)]


def _transit(key: str, start: list[float], end: list[float], alternatives: bool) -> dict:
    """高德公交综合（含地铁）。高德公交需城市编码：本平台研究区以杭州为主 → 330100。"""
    lon, lat = start
    if not (118.0 < lon < 121.0 and 29.0 < lat < 31.2):       # 非杭州(如东京)高德不覆盖
        return {"type": "FeatureCollection", "features": [],
                "meta": {"error": "高德公交/地铁目前仅支持杭州研究区"}}
    import requests
    o = wgs84_to_gcj02(start[0], start[1])
    d = wgs84_to_gcj02(end[0], end[1])
    params = {"key": key, "origin": f"{o[0]:.6f},{o[1]:.6f}", "destination": f"{d[0]:.6f},{d[1]:.6f}",
              "city1": "330100", "city2": "330100", "show_fields": "cost,polyline"}
    try:
        r = requests.get("https://restapi.amap.com/v5/direction/transit/integrated",
                         params=params, timeout=20)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        return {"type": "FeatureCollection", "features": [],
                "meta": {"error": f"调用高德公交失败：{type(e).__name__}: {e}"}}
    if str(data.get("status")) != "1":
        return {"type": "FeatureCollection", "features": [],
                "meta": {"error": f"高德：{data.get('info') or '公交查询失败'}"}}
    transits = ((data.get("route") or {}).get("transits")) or []
    if not transits:
        return {"type": "FeatureCollection", "features": [], "meta": {"error": "未找到公交/地铁方案"}}
    if not alternatives:
        transits = transits[:1]

    tr = transits[0]                                 # 取最优方案；按"段"分别上色比多方案更直观
    cost = tr.get("cost") or {}
    dur = float(cost.get("duration", 0) or 0)
    dist = float(tr.get("distance", 0) or 0)
    fee = cost.get("transit_fee", "")

    feats, legs = [], []
    for seg in tr.get("segments", []):
        # 步行段（灰色）
        wcoords = []
        for st in ((seg.get("walking") or {}).get("steps") or []):
            wcoords += _gcj_polyline_to_wgs84(st.get("polyline"))
        if len(wcoords) >= 2:
            feats.append({"type": "Feature",
                          "geometry": {"type": "LineString", "coordinates": wcoords},
                          "properties": {"seg_type": "walk", "name": "步行",
                                         "mode": "transit", "source": "amap"}})
            legs.append({"type_cn": "步行", "name": "步行", "color": "#9aa7c7"})
        # 公交/地铁段（地铁=蓝，公交=橙）
        for bl in ((seg.get("bus") or {}).get("buslines") or [])[:1]:
            bcoords = _gcj_polyline_to_wgs84(bl.get("polyline"))
            if len(bcoords) < 2:                     # 无线路几何则用站点串接兜底
                stops = [(bl.get("departure_stop") or {}).get("location")] \
                    + [v.get("location") for v in (bl.get("via_stops") or [])] \
                    + [(bl.get("arrival_stop") or {}).get("location")]
                bcoords = [_loc_to_wgs(s) for s in stops if s]
            if len(bcoords) < 2:
                continue
            is_metro = "地铁" in (str(bl.get("type", "")) + str(bl.get("name", "")))
            name = str(bl.get("name", "")).split("(")[0]
            feats.append({"type": "Feature",
                          "geometry": {"type": "LineString", "coordinates": bcoords},
                          "properties": {"seg_type": "subway" if is_metro else "bus",
                                         "name": name, "mode": "transit", "source": "amap"}})
            legs.append({"type_cn": "地铁" if is_metro else "公交", "name": name,
                         "color": "#2d7dff" if is_metro else "#ff9d2e"})

    if not feats:
        return {"type": "FeatureCollection", "features": [], "meta": {"error": "公交方案缺少可绘制几何"}}
    ride = " → ".join(l["name"] for l in legs if l["type_cn"] != "步行")
    return {"type": "FeatureCollection", "features": feats,
            "meta": {"mode": "transit", "mode_cn": "公交/地铁", "source": "amap",
                     "length_m": round(dist, 1), "time_min": round(dur / 60.0, 1),
                     "fee": fee, "legs": legs, "strategy_cn": ride or "公交/地铁"}}


def route(start: list[float], end: list[float], optimize: str = "time",
          mode: str = "drive", vias: list[list[float]] | None = None,
          alternatives: bool = False) -> dict:
    """调用高德 v5 规划路线，输入/输出均为 WGS84 [lon,lat]。

    驾车 alternatives=True 时用 alternative_route=3 返回多条备选（与高德网页版一致）；
    公交/地铁(transit) 走高德公交综合接口（含杭州地铁）。
    """
    key = _amap_key()
    if not key:
        return {"type": "FeatureCollection", "features": [],
                "meta": {"error": "未配置 AMAP_KEY，请在 backend/.env 添加 AMAP_KEY=你的Key 后重启后端"}}
    if mode == "transit":
        return _transit(key, start, end, alternatives)
    if mode not in _ENDPOINT:
        return {"type": "FeatureCollection", "features": [],
                "meta": {"error": f"高德在线暂不支持 {MODE_CN.get(mode, mode)}"}}

    import requests
    o = wgs84_to_gcj02(start[0], start[1])
    d = wgs84_to_gcj02(end[0], end[1])
    params = {"key": key,
              "origin": f"{o[0]:.6f},{o[1]:.6f}",
              "destination": f"{d[0]:.6f},{d[1]:.6f}",
              "show_fields": "polyline,cost"}     # 必须显式要 polyline，否则没几何
    if mode == "drive":
        params["alternative_route"] = 3 if alternatives else 1   # 一次最多返回 3 条
        if vias:                                 # 途经点（GCJ-02，分号分隔）
            gv = [wgs84_to_gcj02(v[0], v[1]) for v in vias]
            params["waypoints"] = ";".join(f"{x:.6f},{y:.6f}" for x, y in gv)
    try:
        r = requests.get(_ENDPOINT[mode], params=params, timeout=20)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        return {"type": "FeatureCollection", "features": [],
                "meta": {"error": f"调用高德失败：{type(e).__name__}: {e}"}}

    paths, err = _parse_paths(data, mode)
    if err:
        return {"type": "FeatureCollection", "features": [], "meta": {"error": f"高德：{err}"}}

    # 排序：最快→按时长；最短→按距离。主推（rank0）即用户当前偏好的最优那条。
    paths.sort(key=lambda p: p[0] if optimize == "length" else p[1])
    if not alternatives:
        paths = paths[:1]

    features, alts = [], []
    for i, (dist_m, dur_s, line) in enumerate(paths):
        coords = _gcj_polyline_to_wgs84(line)
        if len(coords) < 2:
            continue
        label = f"方案{i + 1}"
        features.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {
                "length_m": round(dist_m, 1), "time_min": round(dur_s / 60.0, 1),
                "optimize": optimize, "mode": mode, "mode_cn": MODE_CN.get(mode, mode),
                "source": "amap", "strategy_cn": label, "rank": i,
            },
        })
        alts.append({"strategy_cn": label, "length_km": round(dist_m / 1000.0, 2),
                     "time_min": round(dur_s / 60.0, 1)})

    if not features:
        return {"type": "FeatureCollection", "features": [], "meta": {"error": "高德返回的路线为空"}}
    return {"type": "FeatureCollection", "features": features,
            "meta": {"optimize": optimize, "mode": mode, "mode_cn": MODE_CN.get(mode, mode),
                     "source": "amap", "vias": len(vias or []), "alts": alts}}
