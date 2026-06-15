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

from ..config import CITIES, DEFAULT_CITY, settings
from . import value, routing, stats, service_area, flood

# ---- 1. 工具（函数）注册：交给大模型的"能力清单" ---------------------------

TOOLS = [
    {"type": "function", "function": {
        "name": "assess_value",
        "description": "评估研究区每个网格的住房/地段综合价值（缓冲+距离衰减+加权叠加），返回价值热力网格。",
        "parameters": {"type": "object", "properties": {
            "city": {"type": "string", "enum": list(CITIES.keys())},
        }, "required": ["city"]}}},
    {"type": "function", "function": {
        "name": "site_selection",
        "description": "选址分析：筛选价值高于阈值的优质地块，可取分数最高的前 K 个。",
        "parameters": {"type": "object", "properties": {
            "city": {"type": "string", "enum": list(CITIES.keys())},
            "min_score": {"type": "number", "description": "分数阈值 0-100，默认 70"},
            "top_k": {"type": "integer", "description": "仅取最高的 K 个地块"},
        }, "required": ["city"]}}},
    {"type": "function", "function": {
        "name": "route",
        "description": "通勤路径规划：计算两点间最短时间或最短距离路线。",
        "parameters": {"type": "object", "properties": {
            "city": {"type": "string", "enum": list(CITIES.keys())},
            "start": {"type": "array", "items": {"type": "number"}, "description": "[lon,lat] 起点"},
            "end": {"type": "array", "items": {"type": "number"}, "description": "[lon,lat] 终点"},
            "optimize": {"type": "string", "enum": ["time", "length"]},
        }, "required": ["city", "start", "end"]}}},
    {"type": "function", "function": {
        "name": "evacuate",
        "description": "撤离路径规划：从起点就近前往最近的应急避难场所，可避开危险区。",
        "parameters": {"type": "object", "properties": {
            "city": {"type": "string", "enum": list(CITIES.keys())},
            "start": {"type": "array", "items": {"type": "number"}},
        }, "required": ["city", "start"]}}},
    {"type": "function", "function": {
        "name": "hotspot",
        "description": "对价值评估结果做热点/空间自相关分析（Moran's I + Getis-Ord Gi*），识别高值/低值聚集区。",
        "parameters": {"type": "object", "properties": {
            "city": {"type": "string", "enum": list(CITIES.keys())},
        }, "required": ["city"]}}},
    {"type": "function", "function": {
        "name": "isochrone",
        "description": "服务区/等时圈：从某设施点出发 N 分钟可达范围。",
        "parameters": {"type": "object", "properties": {
            "city": {"type": "string", "enum": list(CITIES.keys())},
            "center": {"type": "array", "items": {"type": "number"}},
            "bands": {"type": "array", "items": {"type": "number"}, "description": "分钟档位，如 [5,10,15]"},
        }, "required": ["city", "center"]}}},
    {"type": "function", "function": {
        "name": "flood",
        "description": "洪水淹没模拟：给定水位计算淹没范围。",
        "parameters": {"type": "object", "properties": {
            "city": {"type": "string", "enum": list(CITIES.keys())},
            "water_level": {"type": "number", "description": "水位（米），默认 6"},
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


# ---- 2. 工具分发：把模型选定的函数真正执行 ---------------------------------

def _dispatch(name: str, args: dict, context_point=None) -> dict:
    """执行指定分析函数，返回 {layer, kind} 供前端渲染。"""
    city = args.get("city", DEFAULT_CITY)

    def pt(key):  # 取坐标参数，缺失时回退到 context_point 或城市中心
        return args.get(key) or context_point or CITIES[city]["center"]

    if name == "assess_value":
        return {"kind": "value", "layer": value.assess_value(city)}
    if name == "site_selection":
        return {"kind": "site", "layer": value.site_selection(
            city, args.get("min_score", 70), top_k=args.get("top_k"))}
    if name == "route":
        return {"kind": "route", "layer": routing.route(
            city, pt("start"), pt("end"), args.get("optimize", "time"))}
    if name == "evacuate":
        return {"kind": "evacuate", "layer": routing.evacuate(city, pt("start"))}
    if name == "hotspot":
        grid = value.assess_value(city)
        return {"kind": "hotspot", "layer": stats.hotspot(grid)}
    if name == "isochrone":
        return {"kind": "isochrone", "layer": service_area.isochrone(
            city, pt("center"), tuple(args.get("bands", (5, 10, 15))))}
    if name == "flood":
        return {"kind": "flood", "layer": flood.simulate(city, args.get("water_level", 6.0))}
    raise ValueError(f"未知工具 {name}")


# ---- 3. Mock 规则引擎（无 Key 时的降级方案） -------------------------------

def _mock_plan(prompt: str) -> tuple[str, dict]:
    """用关键词粗略判断意图，返回 (函数名, 参数)。"""
    p = prompt.lower()
    city = "tokyo" if ("东京" in prompt or "tokyo" in p) else DEFAULT_CITY
    if any(k in prompt for k in ["撤离", "逃生", "避难", "疏散"]):
        return "evacuate", {"city": city}
    if any(k in prompt for k in ["淹没", "洪水", "内涝", "水位"]):
        return "flood", {"city": city}
    if any(k in prompt for k in ["热点", "聚集", "自相关", "moran"]):
        return "hotspot", {"city": city}
    if any(k in prompt for k in ["等时", "服务区", "分钟", "可达"]):
        return "isochrone", {"city": city}
    if any(k in prompt for k in ["选址", "筛选", "优质", "高价值", "最好的"]):
        return "site_selection", {"city": city, "min_score": 75}
    if any(k in prompt for k in ["路线", "路径", "通勤", "怎么走", "导航"]):
        return "route", {"city": city}
    return "assess_value", {"city": city}


# ---- 4. 对外入口 -----------------------------------------------------------

TOOL_CN = {
    "assess_value": "地段价值评估", "site_selection": "选址分析", "route": "通勤路径规划",
    "evacuate": "撤离路径规划", "hotspot": "热点/空间自相关", "isochrone": "服务区/等时圈",
    "flood": "洪水淹没模拟",
}


def _mock_result(prompt: str, city: str, context_point, steps: list, note: str | None = None) -> dict:
    """用关键词规则引擎执行一次分析并组织返回（无 Key 或在线服务不可用时使用）。"""
    name, args = _mock_plan(prompt)
    args.setdefault("city", city)
    steps.append(f"选择工具：{TOOL_CN.get(name, name)}")
    steps.append("执行空间分析引擎")
    result = _dispatch(name, args, context_point)
    steps.append("生成回答并联动地图")
    base = f"已为你执行「{TOOL_CN.get(name, name)}」，结果已在地图上高亮。"
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
        return _mock_result(prompt, city, context_point, steps)

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
        args.setdefault("city", city)
        steps.append(f"选择工具：{TOOL_CN.get(name, name)}")
        steps.append(f"调用参数：{json.dumps(args, ensure_ascii=False)}")
        steps.append("执行空间分析引擎")
        result = _dispatch(name, args, context_point)

        summary = result["layer"].get("meta", {}) if isinstance(result.get("layer"), dict) else {}
        history.append({"role": "tool", "tool_call_id": call.id,
                        "content": json.dumps(summary, ensure_ascii=False)})
        second = client.chat.completions.create(model=settings.deepseek_model, messages=history)
        reply = second.choices[0].message.content
        history.append({"role": "assistant", "content": reply or ""})
        steps.append("生成回答并联动地图")
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
                            note=f"（DeepSeek 暂不可用：{reason}；已用本地规则引擎完成）")


def _trim(history: list) -> None:
    """保留 system + 最近 MAX_HISTORY 条，控制上下文长度。"""
    if len(history) > MAX_HISTORY + 1:
        del history[1:len(history) - MAX_HISTORY]


def reset_session(session_id: str = "default") -> None:
    SESSIONS.pop(session_id, None)
