"""
AI 空间分析助手。

把用户的自然语言转成对分析引擎的调用：
  1. 将各分析功能注册为大模型可调用的"工具"（function calling 规范）；
  2. 调用 DeepSeek，由模型判断该调哪个工具、填哪些参数；
  3. 本地执行该工具，得到 GeoJSON 结果；
  4. 让模型用一句话解释结果，连同图层一起返回前端做高亮联动。

若未配置 DEEPSEEK_API_KEY，则降级为基于关键词的 Mock 规则引擎，
保证整条前后端联动链路在无 Key 时也能演示。
"""
from __future__ import annotations

import json
import math
import re
from copy import deepcopy

from ..config import CITIES, DEFAULT_CITY, settings
from . import value, routing, stats, service_area, flood

# ---- 1. 工具（函数）注册：交给大模型的"能力清单" ---------------------------

TOOLS = [
    {"type": "function", "function": {
        "name": "assess_value",
        "description": "评估研究区每个网格的住房/地段综合价值（缓冲+距离衰减+加权叠加），返回价值热力网格。",
        "parameters": {"type": "object", "properties": {
            "city": {"type": "string", "enum": list(CITIES.keys())},
            "resolution": {"type": "integer", "description": "网格分辨率，默认 48"},
        }, "required": ["city"]}}},
    {"type": "function", "function": {
        "name": "site_selection",
        "description": "选址分析：筛选价值高于阈值的优质地块，可取分数最高的前 K 个。",
        "parameters": {"type": "object", "properties": {
            "city": {"type": "string", "enum": list(CITIES.keys())},
            "min_score": {"type": "number", "description": "分数阈值 0-100，默认 70"},
            "top_k": {"type": "integer", "description": "仅取最高的 K 个地块"},
            "resolution": {"type": "integer", "description": "网格分辨率，默认 48"},
        }, "required": ["city"]}}},
    {"type": "function", "function": {
        "name": "route",
        "description": "通勤路径规划：计算两点间最短时间或最短距离路线。",
        "parameters": {"type": "object", "properties": {
            "city": {"type": "string", "enum": list(CITIES.keys())},
            "start": {"type": "array", "items": {"type": "number"}, "description": "[lon,lat] 起点"},
            "end": {"type": "array", "items": {"type": "number"}, "description": "[lon,lat] 终点"},
            "optimize": {"type": "string", "enum": ["time", "length"]},
            "mode": {"type": "string", "enum": ["drive", "cycle", "walk", "transit"]},
        }, "required": ["city", "start", "end"]}}},
    {"type": "function", "function": {
        "name": "evacuate",
        "description": "撤离路径规划：从起点就近前往最近的应急避难场所，可避开危险区。",
        "parameters": {"type": "object", "properties": {
            "city": {"type": "string", "enum": list(CITIES.keys())},
            "start": {"type": "array", "items": {"type": "number"}},
            "mode": {"type": "string", "enum": ["drive", "cycle", "walk", "transit"]},
        }, "required": ["city", "start"]}}},
    {"type": "function", "function": {
        "name": "hotspot",
        "description": "对价值评估结果做热点/空间自相关分析（Moran's I + Getis-Ord Gi*），识别高值/低值聚集区。",
        "parameters": {"type": "object", "properties": {
            "city": {"type": "string", "enum": list(CITIES.keys())},
            "attr": {"type": "string", "enum": ["score", "scenic", "commercial", "school", "hospital", "transit", "road"]},
            "k": {"type": "integer", "description": "近邻数量，默认 8"},
        }, "required": ["city"]}}},
    {"type": "function", "function": {
        "name": "isochrone",
        "description": "服务区/等时圈：从某设施点出发 N 分钟可达范围。",
        "parameters": {"type": "object", "properties": {
            "city": {"type": "string", "enum": list(CITIES.keys())},
            "center": {"type": "array", "items": {"type": "number"}},
            "bands": {"type": "array", "items": {"type": "number"}, "description": "分钟档位，如 [5,10,15]"},
            "mode": {"type": "string", "enum": ["drive", "cycle", "walk", "transit"]},
        }, "required": ["city", "center"]}}},
    {"type": "function", "function": {
        "name": "flood",
        "description": "洪水淹没模拟：给定水位计算淹没范围。",
        "parameters": {"type": "object", "properties": {
            "city": {"type": "string", "enum": list(CITIES.keys())},
            "water_level": {"type": "number", "description": "水位（米），默认 6"},
            "resolution": {"type": "integer", "description": "淹没分辨率，默认 100"},
        }, "required": ["city"]}}},
]

SYSTEM_PROMPT = (
    "你是一个城市三维 GIS 平台的空间分析助手。根据用户的中文需求，选择最合适的一个分析工具并填好参数。"
    "若用户未指定区域，默认使用 '" + DEFAULT_CITY + "'。"
    "你能记住本次对话的上下文：用户可能基于上一步结果继续追问（如'那再把水位调到8米''换成最短路径'），"
    "请结合历史消息理解其指代。"
    "若用户提到'当前点/这里/我选的点'，使用上下文中提供的 context_point 作为坐标。"
    "调用工具后，用一两句简洁中文向用户说明结果要点。"
)

# 简单的服务端多轮会话存储：session_id -> messages 历史
SESSIONS: dict[str, list] = {}
MAX_HISTORY = 14   # 控制上下文长度
MOCK_STATE: dict[str, dict] = {}


def _city_aliases() -> dict[str, str]:
    aliases = {
        "杭州都市区": "hangzhou_metro",
        "杭州市都市区": "hangzhou_metro",
        "都市区": "hangzhou_metro",
        "杭州全域": "hangzhou_full",
        "杭州市全域": "hangzhou_full",
        "全域": "hangzhou_full",
        "杭州主城区": "hangzhou_core",
        "杭州市主城区": "hangzhou_core",
        "主城区": "hangzhou_core",
        "杭州主城": "hangzhou_core",
        "杭州市主城": "hangzhou_core",
        "主城": "hangzhou_core",
        "杭州市": "hangzhou_core",
        "杭州": "hangzhou_core",
        "东京": "tokyo",
        "tokyo": "tokyo",
    }
    for key, meta in CITIES.items():
        aliases[key.lower()] = key
        aliases[str(meta.get("name", "")).lower()] = key
    return aliases


CITY_ALIASES = _city_aliases()


def _clamp_num(v, lo: float, hi: float, default: float) -> float:
    try:
        if v is None:
            return default
        x = float(v)
        if not math.isfinite(x):
            return default
        return min(max(x, lo), hi)
    except Exception:
        return default


def _clamp_int(v, lo: int, hi: int, default: int) -> int:
    return int(round(_clamp_num(v, lo, hi, default)))


def _extract_city(prompt: str, fallback: str = DEFAULT_CITY) -> str:
    p = prompt.lower()
    for alias, city in sorted(CITY_ALIASES.items(), key=lambda item: len(item[0]), reverse=True):
        if alias and alias in p:
            return city
    return fallback if fallback in CITIES else DEFAULT_CITY


def _extract_numbers(prompt: str) -> list[float]:
    return [float(x) for x in re.findall(r"[-+]?\d+(?:\.\d+)?", prompt)]


def _extract_coord(prompt: str) -> list[float] | None:
    matches = re.findall(r"([-+]?\d+(?:\.\d+)?)\s*[,，]\s*([-+]?\d+(?:\.\d+)?)", prompt)
    for a, b in matches:
        lon, lat = float(a), float(b)
        if 70 <= lon <= 150 and 0 <= lat <= 60:
            return [lon, lat]
    return None


def _infer_mode(prompt: str) -> str:
    if any(k in prompt for k in ["步行", "走路", "徒步"]):
        return "walk"
    if any(k in prompt for k in ["骑行", "自行车", "单车"]):
        return "cycle"
    if any(k in prompt for k in ["公交", "地铁", "公共交通"]):
        return "transit"
    return "drive"


def _infer_optimize(prompt: str) -> str:
    if any(k in prompt for k in ["最短", "距离短", "少走"]):
        return "length"
    return "time"


def _infer_attr(prompt: str) -> str:
    mapping = {
        "景点": "scenic", "旅游": "scenic",
        "商业": "commercial", "商圈": "commercial",
        "学校": "school", "学区": "school",
        "医院": "hospital", "医疗": "hospital",
        "交通": "transit", "公交": "transit", "地铁": "transit",
        "道路": "road", "主干道": "road",
    }
    for key, attr in mapping.items():
        if key in prompt:
            return attr
    return "score"


def _city_point(city: str, dx: float = 0.0, dy: float = 0.0) -> list[float]:
    lon, lat = CITIES[city]["center"]
    return [round(lon + dx, 6), round(lat + dy, 6)]


def _safe_point(city: str, point, dx: float = 0.0, dy: float = 0.0) -> list[float]:
    if isinstance(point, (list, tuple)) and len(point) >= 2:
        try:
            lon, lat = float(point[0]), float(point[1])
            if math.isfinite(lon) and math.isfinite(lat):
                return [lon, lat]
        except Exception:
            pass
    return _city_point(city, dx, dy)


def _summarize_layer(kind: str, layer: dict) -> str:
    meta = layer.get("meta", {}) if isinstance(layer, dict) else {}
    if kind == "value":
        return f"均值 {meta.get('score_mean', '-')}, P90 {meta.get('score_p90', '-')}, 最高 {meta.get('score_max', '-')}。"
    if kind == "site":
        return f"筛出 {meta.get('count', 0)} 个地块，最高分 {meta.get('top_score', 0)}。"
    if kind == "flood":
        return f"淹没面积约 {meta.get('flooded_area_km2', 0)} km²，淹没单元 {meta.get('flooded_cells', 0)} 个。"
    if kind in ("route", "evacuate"):
        feats = layer.get("features", [])
        line = feats[0].get("properties", {}) if feats else {}
        if line:
            return f"路线约 {line.get('length_m', 0)} 米，预计 {line.get('time_min', 0)} 分钟。"
        return meta.get("error", "未找到可达路线。")
    if kind == "isochrone":
        return f"生成 {len(layer.get('features', []))} 个等时圈。"
    if kind == "hotspot":
        return f"Moran's I={meta.get('moran_i', '-')}, 热点统计字段 {meta.get('attr', 'score')}。"
    return "结果已生成。"


# ---- 2. 工具分发：把模型选定的函数真正执行 ---------------------------------

def _dispatch(name: str, args: dict, context_point=None) -> dict:
    """执行指定分析函数，返回 {layer, kind} 供前端渲染。"""
    city = args.get("city", DEFAULT_CITY)
    if city not in CITIES:
        city = DEFAULT_CITY

    def pt(key):  # 取坐标参数，缺失时回退到 context_point 或城市中心
        return _safe_point(city, args.get(key) or context_point)

    if name == "assess_value":
        resolution = _clamp_int(args.get("resolution"), 24, 96, 48)
        return {"kind": "value", "layer": value.assess_value(city, resolution=resolution)}
    if name == "site_selection":
        resolution = _clamp_int(args.get("resolution"), 24, 96, 48)
        return {"kind": "site", "layer": value.site_selection(
            city,
            _clamp_num(args.get("min_score"), 0, 100, 70),
            resolution=resolution,
            top_k=_clamp_int(args.get("top_k"), 1, 200, 30) if args.get("top_k") else None)}
    if name == "route":
        start = _safe_point(city, args.get("start") or context_point, -0.04, 0.0)
        end = _safe_point(city, args.get("end"), 0.04, 0.02)
        if start == end:
            end = _city_point(city, 0.05, 0.02)
        return {"kind": "route", "layer": routing.route(
            city, start, end, args.get("optimize", "time"), mode=args.get("mode", "drive"))}
    if name == "evacuate":
        return {"kind": "evacuate", "layer": routing.evacuate(
            city, pt("start"), mode=args.get("mode", "drive"))}
    if name == "hotspot":
        grid = value.assess_value(city, resolution=_clamp_int(args.get("resolution"), 24, 96, 48))
        return {"kind": "hotspot", "layer": stats.hotspot(
            grid, attr=args.get("attr", "score"), k=_clamp_int(args.get("k"), 3, 16, 8))}
    if name == "isochrone":
        return {"kind": "isochrone", "layer": service_area.isochrone(
            city, pt("center"), tuple(args.get("bands", (5, 10, 15))), mode=args.get("mode", "drive"))}
    if name == "flood":
        return {"kind": "flood", "layer": flood.simulate(
            city,
            _clamp_num(args.get("water_level"), 0.5, 40, 6.0),
            _clamp_int(args.get("resolution"), 40, 160, 100))}
    raise ValueError(f"未知工具 {name}")


# ---- 3. Mock 规则引擎（无 Key 时的降级方案） -------------------------------

def _mock_plan(prompt: str, city: str = DEFAULT_CITY, context_point=None,
               previous: dict | None = None) -> tuple[str, dict]:
    """用关键词粗略判断意图，返回 (函数名, 参数)。"""
    city = _extract_city(prompt, city)
    args: dict = {"city": city}
    nums = _extract_numbers(prompt)
    coord = _extract_coord(prompt)

    if previous and any(k in prompt for k in ["再", "继续", "换成", "改成", "调到", "提高", "降低"]):
        name = previous.get("tool") or "assess_value"
        args.update(deepcopy(previous.get("args") or {}))
        args["city"] = city
    else:
        name = ""

    if any(k in prompt for k in ["撤离", "逃生", "避难", "疏散"]):
        name = "evacuate"
    elif any(k in prompt for k in ["淹没", "洪水", "内涝", "水位", "涨水"]):
        name = "flood"
    elif any(k in prompt for k in ["热点", "聚集", "自相关", "moran", "冷点"]):
        name = "hotspot"
    elif any(k in prompt for k in ["等时", "服务区", "分钟", "可达"]):
        name = "isochrone"
    elif any(k in prompt for k in ["选址", "筛选", "优质", "高价值", "最好的", "地块"]):
        name = "site_selection"
    elif any(k in prompt for k in ["路线", "路径", "通勤", "怎么走", "导航", "最短", "最快"]):
        name = "route"
    elif not name:
        name = "assess_value"

    if name in ("route", "evacuate", "isochrone"):
        args["mode"] = _infer_mode(prompt)
    if name == "route":
        args["optimize"] = _infer_optimize(prompt)
        args["start"] = coord or context_point or args.get("start") or _city_point(city, -0.04, 0.0)
        args["end"] = args.get("end") or _city_point(city, 0.04, 0.02)
    elif name == "evacuate":
        args["start"] = coord or context_point or args.get("start") or CITIES[city]["center"]
    elif name == "isochrone":
        args["center"] = coord or context_point or args.get("center") or CITIES[city]["center"]
        minutes = [int(n) for n in nums if 1 <= n <= 120]
        if minutes:
            if len(minutes) == 1:
                m = minutes[0]
                args["bands"] = sorted(set([max(5, round(m / 3)), max(5, round(m * 2 / 3)), m]))
            else:
                args["bands"] = sorted(set(minutes[:4]))
        else:
            args.setdefault("bands", [5, 10, 15])
    elif name == "flood":
        level_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:米|m|M)", prompt)
        if level_match:
            args["water_level"] = float(level_match.group(1))
        elif nums:
            args["water_level"] = nums[0]
        else:
            args.setdefault("water_level", 6.0)
    elif name == "site_selection":
        if nums:
            if any(k in prompt for k in ["前", "top", "Top", "TOP", "个"]):
                args["top_k"] = int(nums[0])
                args.setdefault("min_score", 60)
            else:
                args["min_score"] = nums[0]
        else:
            args.setdefault("min_score", 75)
        args.setdefault("top_k", 30)
    elif name == "hotspot":
        args["attr"] = _infer_attr(prompt)
        if nums:
            args["k"] = int(nums[0])

    if "高分辨率" in prompt or "精细" in prompt:
        args["resolution"] = 120 if name == "flood" else 72
    elif "快速" in prompt or "粗略" in prompt:
        args["resolution"] = 70 if name == "flood" else 36

    return name, args


# ---- 4. 对外入口 -----------------------------------------------------------

TOOL_CN = {
    "assess_value": "地段价值评估", "site_selection": "选址分析", "route": "通勤路径规划",
    "evacuate": "撤离路径规划", "hotspot": "热点/空间自相关", "isochrone": "服务区/等时圈",
    "flood": "洪水淹没模拟",
}


def _mock_result(prompt: str, city: str, context_point, steps: list,
                 note: str | None = None, session_id: str = "default") -> dict:
    """用关键词规则引擎执行一次分析并组织返回（无 Key 或在线服务不可用时使用）。"""
    previous = MOCK_STATE.get(session_id)
    name, args = _mock_plan(prompt, city, context_point, previous)
    args.setdefault("city", city)
    steps.append(f"选择工具：{TOOL_CN.get(name, name)}")
    steps.append(f"调用参数：{json.dumps(args, ensure_ascii=False)}")
    steps.append("执行空间分析引擎")
    try:
        result = _dispatch(name, args, context_point)
    except Exception as exc:
        steps.append(f"执行失败：{type(exc).__name__}: {exc}")
        return {"reply": f"无法完成「{TOOL_CN.get(name, name)}」：{exc}",
                "tool": name, "args": args, "engine": "mock(降级)" if note else "mock",
                "kind": None, "layer": None, "steps": steps}
    steps.append("生成回答并联动地图")
    MOCK_STATE[session_id] = {"tool": name, "args": deepcopy(args)}
    base = f"已为你执行「{TOOL_CN.get(name, name)}」，{_summarize_layer(result['kind'], result['layer'])}结果已在地图上高亮。"
    return {"reply": (note + base) if note else base, "tool": name, "args": args,
            "engine": "mock(降级)" if note else "mock", "steps": steps, **result}


def analyze(prompt: str, city: str = DEFAULT_CITY, context_point=None,
            session_id: str = "default") -> dict:
    """
    AI 空间分析主入口（多轮上下文 + 执行步骤反馈）。
    返回 {reply, kind, layer, tool, args, engine, steps}

    任何一步失败（openai 未安装 / Key 无效 / 网络不可达等）都会优雅降级为本地规则引擎，
    保证前后端联动链路始终可用，不会抛 500。
    """
    steps = ["理解需求与上下文"]

    if not settings.deepseek_api_key:
        return _mock_result(prompt, city, context_point, steps, session_id=session_id)

    # ---- DeepSeek 分支（function calling + 会话历史）----
    try:
        from openai import OpenAI
        import httpx
        # openai 1.35.3 初始化时会向 httpx 传 proxies；而新版 httpx(>=0.28) 已删除该参数，
        # 直接 OpenAI() 会抛 “TypeError: Client.__init__() got an unexpected keyword argument 'proxies'”
        # （这正是 AI 由可用变不可用的原因——httpx 被升级了）。传入自建 http_client 即可绕过。
        client = OpenAI(api_key=settings.deepseek_api_key,
                        base_url=settings.deepseek_base_url,
                        http_client=httpx.Client(timeout=60.0))

        history = SESSIONS.setdefault(session_id, [{"role": "system", "content": SYSTEM_PROMPT}])
        user_content = prompt + (f"\n[context_point={context_point}]" if context_point else "")
        history.append({"role": "user", "content": user_content})

        first = client.chat.completions.create(
            model=settings.deepseek_model, messages=history, tools=TOOLS, tool_choice="auto")
        msg = first.choices[0].message
        tool_calls = msg.tool_calls or []

        if not tool_calls:            # 没有可执行的分析，直接回话
            history.append({"role": "assistant", "content": msg.content or ""})
            steps.append("直接回答（无需调用分析工具）")
            _trim(history)
            return {"reply": msg.content or "未能识别可执行的空间分析意图。", "tool": None,
                    "args": {}, "engine": "deepseek", "kind": None, "layer": None, "steps": steps}

        # 以纯字典形式回写助手消息（只保留第一个 tool_call，确保与下方 tool 消息一一配对）；
        # 直接把 SDK 的消息对象塞回 messages 容易导致再次请求时序列化/配对出错。
        call = tool_calls[0]
        history.append({"role": "assistant", "content": msg.content or "",
                        "tool_calls": [{"id": call.id, "type": "function",
                                        "function": {"name": call.function.name,
                                                     "arguments": call.function.arguments}}]})
        name = call.function.name
        args = json.loads(call.function.arguments or "{}")
        args["city"] = _extract_city(prompt, args.get("city") or city)
        steps.append(f"选择工具：{TOOL_CN.get(name, name)}")
        steps.append(f"调用参数：{json.dumps(args, ensure_ascii=False)}")
        steps.append("执行空间分析引擎")
        try:
            result = _dispatch(name, args, context_point)
        except Exception as exc:
            steps.append(f"执行失败：{type(exc).__name__}: {exc}")
            reply = f"我识别到需要执行「{TOOL_CN.get(name, name)}」，但分析引擎返回错误：{exc}"
            history.append({"role": "assistant", "content": reply})
            _trim(history)
            return {"reply": reply, "tool": name, "args": args, "engine": "deepseek",
                    "kind": None, "layer": None, "steps": steps}

        summary = result["layer"].get("meta", {}) if isinstance(result.get("layer"), dict) else {}
        history.append({"role": "tool", "tool_call_id": call.id,
                        "content": json.dumps(summary, ensure_ascii=False)})
        second = client.chat.completions.create(model=settings.deepseek_model, messages=history)
        reply = second.choices[0].message.content
        history.append({"role": "assistant", "content": reply or ""})
        steps.append("生成回答并联动地图")
        MOCK_STATE[session_id] = {"tool": name, "args": deepcopy(args)}
        _trim(history)
        return {"reply": reply, "tool": name, "args": args, "engine": "deepseek", "steps": steps, **result}

    except Exception as e:
        # 把真实错误打到后端日志（鉴权失败/网络不可达/参数错误等），并随回复返回，便于定位；
        # 同时降级到本地规则引擎，保证仍有结果与地图联动。
        import traceback
        traceback.print_exc()
        SESSIONS.pop(session_id, None)
        reason = f"{type(e).__name__}: {e}"
        steps.append(f"DeepSeek 调用失败：{reason}（已降级本地规则引擎）")
        return _mock_result(prompt, city, context_point, steps,
                            note=f"（DeepSeek 暂不可用：{reason}；已用本地规则引擎完成）",
                            session_id=session_id)


def _trim(history: list) -> None:
    """保留 system + 最近 MAX_HISTORY 条，控制上下文长度。"""
    if len(history) > MAX_HISTORY + 1:
        del history[1:len(history) - MAX_HISTORY]


def reset_session(session_id: str = "default") -> None:
    SESSIONS.pop(session_id, None)
    MOCK_STATE.pop(session_id, None)
