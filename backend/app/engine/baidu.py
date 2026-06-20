"""
百度地图「批量算路」(Route Matrix v2) 校准的真实路况服务区（仅国内）。

策略：**百度校准耗时，本地路网成面**（慢一点但稳定、形状贴合路网，不跨水体/山体）。
  1) 本地路网单源 Dijkstra，得到中心点周边各路网节点的离线可达时间（已含拥堵系数）；
  2) 从可达节点里按时间分层抽样若干节点，**串行 + 限速 + 退避重试**地调百度批量算路，
     取这些节点的真实(含实时路况)耗时；
  3) 用"真实/离线"耗时的中位数比值，校准全部可达节点的耗时（全局拥堵系数）；
  4) 每个时间档用"校准耗时 ≤ 档位"的**真实路网节点**凹包成面——覆盖请求的全部档位，绝不静默丢档；
  5) 若百度有效样本不足/被限流，则离线补全并在 meta.note 明示，绝不假装成功。

约束：所有百度请求**串行**（绝不并发），批次间 sleep 限速，401/并发超配额按 2/4/8s 退避重试。
坐标以 WGS84 传入（coord_type=wgs84）；接口仅返回耗时/距离，无需坐标反转换。
配置：backend/.env 写 BAIDU_AK=服务端AK，并在控制台开通「批量算路」。
"""
from __future__ import annotations

import json
import math
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

import networkx as nx

from . import routing
from . import service_area as _sa          # 别名，避免与下方 service_area() 函数同名冲突
from .routing import MODE_CN

_BASE = "https://api.map.baidu.com/routematrix/v2/"
_MODE_PATH = {"drive": "driving", "walk": "walking", "cycle": "riding"}
_CHUNK = 20                 # 每批终点数（百度批量算路单批保守取值）
_REQ_DELAY = 0.8            # 批次间隔(秒)：串行限速，规避 QPS/并发限制
_MAX_RETRY = 3             # 触发"并发超配额"时退避重试次数（2/4/8s）
_SAMPLE_BUDGET = 100       # 校准采样节点数（串行请求次数 ≈ BUDGET/CHUNK）
_CALIB_MIN = 5            # 至少这么多有效样本才信任百度校准，否则离线补全
_CUTOFF_MARGIN = 1.4      # 离线 Dijkstra 截止 = 最大档 × 该系数，给校准缩放留余量


def _baidu_ak() -> str:
    """读取百度 AK：优先环境变量 BAIDU_AK，其次 backend/.env。"""
    ak = os.environ.get("BAIDU_AK", "").strip()
    if ak:
        return ak
    env = Path(__file__).resolve().parents[2] / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith("BAIDU_AK="):
                return s.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("未配置 BAIDU_AK（请在 backend/.env 设置）")


def _fetch_json(url: str) -> dict:
    """单次请求；对"并发超配额(限流)"按 2/4/8s 退避重试。"""
    for attempt in range(_MAX_RETRY + 1):
        with urllib.request.urlopen(url, timeout=25) as r:
            data = json.loads(r.read().decode("utf-8"))
        if data.get("status") == 0:
            return data
        msg = str(data.get("message", ""))
        limited = data.get("status") == 401 or any(k in msg for k in ("并发", "配额", "限流"))
        if attempt < _MAX_RETRY and limited:
            time.sleep(2 ** (attempt + 1))              # 2s, 4s, 8s 退避
            continue
        raise RuntimeError(f"百度批量算路 status={data.get('status')} {msg}")
    raise RuntimeError("百度批量算路重试后仍失败")


def _matrix(center: list[float], dests: list, mode: str) -> list:
    """串行批量算路：返回与 dests 等长的耗时(分钟)，不可达/缺失记 inf。"""
    ak = _baidu_ak()
    path = _MODE_PATH.get(mode, "driving")
    lon0, lat0 = center
    out = []
    for i in range(0, len(dests), _CHUNK):              # 分批，串行
        if i:
            time.sleep(_REQ_DELAY)                      # 批次间限速，绝不并发
        chunk = dests[i:i + _CHUNK]
        params = {
            "origins": f"{lat0:.6f},{lon0:.6f}",        # 百度顺序：纬度,经度
            "destinations": "|".join(f"{la:.6f},{lo:.6f}" for lo, la in chunk),
            "coord_type": "wgs84", "output": "json", "ak": ak,
        }
        data = _fetch_json(_BASE + path + "?" + urllib.parse.urlencode(params))
        res = data.get("result", [])
        for j in range(len(chunk)):                     # 逐项对齐，避免少返回时错位
            dur = (res[j].get("duration") or {}).get("value") if j < len(res) else None
            out.append(float(dur) / 60.0 if dur is not None else math.inf)
    return out


def _sample_nodes(g, reach: dict, bands) -> list:
    """从离线可达节点按时间分层抽样，保证近/远各档都有代表、空间分散。"""
    edges = sorted(bands)
    strata = [[] for _ in edges]
    for n, t in reach.items():
        for i, b in enumerate(edges):                  # 落入第一个 t<=b 的档
            if t <= b:
                strata[i].append((t, n))
                break
    per = max(_SAMPLE_BUDGET // max(len(edges), 1), 10)
    picked, seen = [], set()
    for grp in strata:
        if not grp:
            continue
        grp.sort()                                     # 时间排序后等间隔取，空间上也较分散
        step = max(len(grp) // per, 1)
        for i in range(0, len(grp), step):
            n = grp[i][1]
            if n not in seen:
                seen.add(n)
                picked.append(n)
    return picked[:_SAMPLE_BUDGET]


def service_area(city: str, center: list[float], bands, mode: str = "drive") -> dict:
    """百度校准 + 本地路网成面的服务区（返回结构与 service_area.isochrone 一致）。"""
    bands = sorted({float(b) for b in bands if b and float(b) > 0})
    if not bands:
        return {"type": "FeatureCollection", "features": [],
                "meta": {"city": city, "error": "请至少选择一个有效的时间档"}}
    if mode == "transit":                              # 批量算路无公交口径 → 退驾车
        mode = "drive"

    g = routing.build_graph(city)
    src = routing._nearest_within(g, center[0], center[1])
    if src is None:
        return {"type": "FeatureCollection", "features": [],
                "meta": {"city": city, "error": "中心点不在研究区或离路网过远（请在城区道路附近取点）"}}

    wfn = routing._weight_fn(mode, "time")
    reach = nx.single_source_dijkstra_path_length(
        g, src, cutoff=max(bands) * _CUTOFF_MARGIN, weight=wfn)    # {node: 离线分钟}
    if len(reach) < 3:
        return {"type": "FeatureCollection", "features": [],
                "meta": {"city": city, "error": "该点未连通到路网（换个靠路的点试试）"}}

    # —— 串行调百度，校准耗时 ——
    sample = _sample_nodes(g, reach, bands)
    coords = [(g.nodes[n]["lon"], g.nodes[n]["lat"]) for n in sample]
    n_sampled = len(coords)
    note = ""
    try:
        bmins = _matrix(center, coords, mode)
    except Exception as e:                              # 限流/失败 → 进入离线补全分支
        bmins = [math.inf] * n_sampled
        note = f"百度调用失败（{e}）"

    pairs = [(reach[n], bm) for n, bm in zip(sample, bmins)
             if math.isfinite(bm) and reach[n] > 0]
    n_valid = len(pairs)
    if n_valid >= _CALIB_MIN:                           # 信任百度校准
        rs = sorted(bm / off for off, bm in pairs)
        ratio = rs[len(rs) // 2]                         # 中位数比值（真实/离线）
        source = "baidu"
        note = (f"百度实时路况校准耗时 + 本地路网成面"
                f"（采样 {n_sampled}/有效 {n_valid} 点，拥堵校准 ×{ratio:.2f}）")
    else:                                               # 样本不足 → 离线补全并明示
        ratio = 1.0
        source = "offline_fallback"
        note = ((note + "；") if note else "") + \
               f"百度有效样本不足（{n_valid}/{n_sampled}），已用本地离线路网补全（建议冷却后重试）"

    # —— 用全部真实路网可达节点（校准后耗时）成面，覆盖全部请求档位 ——
    est = [(g.nodes[n]["lon"], g.nodes[n]["lat"], t * ratio) for n, t in reach.items()]
    features = _sa._build_features(est, bands, center[1], clip=_sa._water_clip(city))
    if not features:
        return {"type": "FeatureCollection", "features": [],
                "meta": {"city": city, "error": "可达点过少，未能成面"}}

    got = {f["properties"]["minutes"] for f in features}
    missing = [b for b in bands if b not in got]
    for f in features:
        f["properties"]["source"] = source
        f["properties"]["sampled_points"] = n_sampled
        f["properties"]["baidu_valid_points"] = n_valid
    if missing:
        note += f"；档位 {missing} 可达点过少未能成面"

    return {"type": "FeatureCollection", "features": features,
            "meta": {"city": city, "center": center, "bands": bands,
                     "summary": _sa._summary(features), "mode": mode,
                     "mode_cn": MODE_CN.get(mode, mode), "provider": source, "source": source,
                     "sampled_points": n_sampled, "baidu_valid_points": n_valid,
                     "calib_ratio": round(ratio, 2), "approx": source != "baidu", "note": note}}
