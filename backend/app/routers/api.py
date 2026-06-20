"""
全部 REST 接口。

按模块分组：基础数据 /data、价值与选址 /analysis、路径 /route、
空间统计 /stats、服务区 /service-area、洪水 /flood、AI 助手 /ai。
请求体用 pydantic 模型做校验，返回均为 GeoJSON 或其包装。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from typing import Literal

from pydantic import BaseModel, Field

from ..config import CITIES, DEFAULT_CITY
from ..data_loader import load_pois, load_roads, load_shelters, POI_CN
from ..engine import value, routing, stats, service_area, flood, ai_agent, amap

router = APIRouter(prefix="/api")


# ---------- 请求模型 ----------
class ValueReq(BaseModel):
    city: str = DEFAULT_CITY
    weights: dict[str, float] | None = None
    resolution: int = Field(48, ge=20, le=100)


class SiteReq(BaseModel):
    city: str = DEFAULT_CITY
    min_score: float = 70
    top_k: int | None = None
    weights: dict[str, float] | None = None
    resolution: int = Field(48, ge=20, le=100)


class RouteReq(BaseModel):
    city: str = DEFAULT_CITY
    start: list[float]
    end: list[float]
    optimize: Literal["time", "length"] = "time"
    mode: Literal["drive", "cycle", "walk", "transit"] = "drive"
    vias: list[list[float]] | None = None     # 途径点 [[lon,lat],...]
    alternatives: bool = False                # 返回「最快+最短」两条备选
    hazard: dict | None = None   # 可选 GeoJSON 几何，路径避让


class EvacReq(BaseModel):
    city: str = DEFAULT_CITY
    start: list[float]
    mode: Literal["drive", "cycle", "walk", "transit"] = "drive"
    hazard: dict | None = None


class AmapRouteReq(BaseModel):
    start: list[float]
    end: list[float]
    optimize: Literal["time", "length"] = "time"
    mode: Literal["drive", "walk", "cycle", "transit"] = "drive"
    vias: list[list[float]] | None = None
    alternatives: bool = False                 # 驾车时返回多条备选（最快/最短/躲避拥堵）


class CityReq(BaseModel):
    city: str = DEFAULT_CITY


class HotspotReq(BaseModel):
    city: str = DEFAULT_CITY
    weights: dict[str, float] | None = None
    resolution: int = Field(48, ge=20, le=100)
    k: int = Field(8, ge=2, le=32)
    attr: Literal[
        "score",
        "scenic", "commercial", "school", "hospital", "transit", "road",
        "detail.scenic", "detail.commercial", "detail.school",
        "detail.hospital", "detail.transit", "detail.road",
    ] = "score"


class ServiceReq(BaseModel):
    city: str = DEFAULT_CITY
    center: list[float]
    bands: list[float] = [5, 10, 15]
    mode: Literal["drive", "cycle", "walk", "transit"] = "drive"
    baidu: bool = False                        # True=用百度批量算路(真实路况,仅国内)，否则离线


class FloodReq(BaseModel):
    city: str = DEFAULT_CITY
    water_level: float = Field(6.0, ge=0.5, le=40)
    resolution: int = Field(100, ge=40, le=160)


class FloodAnimReq(BaseModel):
    city: str = DEFAULT_CITY
    target_level: float = Field(8.0, ge=1.0, le=40)
    frames: int = Field(9, ge=2, le=20)
    resolution: int = Field(90, ge=40, le=140)


class AIReq(BaseModel):
    prompt: str
    city: str = DEFAULT_CITY
    context_point: list[float] | None = None
    session_id: str = "default"


def _check_city(city: str):
    if city not in CITIES:
        raise HTTPException(404, f"未知城市 {city}")


# ---------- 元数据 / 基础数据 ----------
@router.get("/cities")
def get_cities():
    """返回所有研究区元数据 + POI 类别中文名，供前端初始化。"""
    return {"cities": CITIES, "default": DEFAULT_CITY, "poi_cn": POI_CN}


@router.get("/data/pois")
def get_pois(city: str = Query(DEFAULT_CITY)):
    _check_city(city)
    return load_pois(city)


@router.get("/data/roads")
def get_roads(city: str = Query(DEFAULT_CITY)):
    _check_city(city)
    return load_roads(city)


@router.get("/data/shelters")
def get_shelters(city: str = Query(DEFAULT_CITY)):
    _check_city(city)
    return load_shelters(city)


# ---------- 价值评估 / 选址 ----------
@router.post("/analysis/value")
def post_value(req: ValueReq):
    _check_city(req.city)
    return value.assess_value(req.city, req.weights, req.resolution)


@router.post("/analysis/site")
def post_site(req: SiteReq):
    _check_city(req.city)
    return value.site_selection(req.city, req.min_score, req.weights, req.resolution, top_k=req.top_k)


# ---------- 路径规划 ----------
@router.post("/route")
def post_route(req: RouteReq):
    _check_city(req.city)
    hazard = routing.to_shape(req.hazard)
    return routing.route(req.city, req.start, req.end, req.optimize, hazard,
                         req.mode, req.vias, req.alternatives)


@router.post("/evacuate")
def post_evacuate(req: EvacReq):
    _check_city(req.city)
    hazard = routing.to_shape(req.hazard)
    return routing.evacuate(req.city, req.start, hazard, req.mode)


@router.post("/route/amap")
def post_route_amap(req: AmapRouteReq):
    """高德在线路径规划（含实时路况下的用时）。坐标 WGS84 进出，内部自动 GCJ-02 转换。"""
    return amap.route(req.start, req.end, req.optimize, req.mode, req.vias, req.alternatives)


# ---------- 空间统计：热点 ----------
@router.post("/stats/hotspot")
def post_hotspot(req: HotspotReq):
    _check_city(req.city)
    grid = value.assess_value(req.city, req.weights, req.resolution)
    return stats.hotspot(grid, attr=req.attr, k=req.k)


# ---------- 服务区 / 等时圈 ----------
@router.post("/service-area")
def post_service_area(req: ServiceReq):
    _check_city(req.city)
    provider = "baidu" if req.baidu else "offline"
    return service_area.isochrone(req.city, req.center, tuple(req.bands), req.mode, provider)


# ---------- 洪水淹没 ----------
@router.post("/flood")
def post_flood(req: FloodReq):
    _check_city(req.city)
    return flood.simulate(req.city, req.water_level, req.resolution)


@router.post("/flood/animation")
def post_flood_animation(req: FloodAnimReq):
    """涨水过程动画：返回从低到目标水位的多帧淹没范围。"""
    _check_city(req.city)
    return flood.simulate_levels(req.city, req.target_level, req.frames, req.resolution)


# ---------- AI 助手 ----------
@router.post("/ai")
def post_ai(req: AIReq):
    _check_city(req.city)
    return ai_agent.analyze(req.prompt, req.city, req.context_point, req.session_id)


@router.post("/ai/reset")
def post_ai_reset(session_id: str = "default"):
    ai_agent.reset_session(session_id)
    return {"ok": True}
