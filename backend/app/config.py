"""全局配置。

研究区（AREAS）以"区域 id"为键，杭州三档 + 东京。
前端可切换研究区；后端按区索引 bbox、UTM、视域默认参数等。
"""
from __future__ import annotations

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BASE_DIR / "data"


class Settings(BaseSettings):
    """从 .env 读取的运行期配置。"""
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"


settings = Settings()


# 研究区元数据。bbox=[minLon,minLat,maxLon,maxLat]，center=[lon,lat]
AREAS = {
    "hangzhou_core": {
        "name": "杭州·主城区", "group": "杭州", "scale": "主城(~30km)",
        "center": [120.16, 30.27],
        "bbox": [120.00, 30.13, 120.42, 30.42],
        "camera_height": 16000, "utm_epsg": 32650,
    },
    "hangzhou_metro": {
        "name": "杭州·都市区", "group": "杭州", "scale": "都市区(~60km)",
        "center": [120.20, 30.25],
        "bbox": [119.78, 30.00, 120.62, 30.52],
        "camera_height": 36000, "utm_epsg": 32650,
    },
    "hangzhou_full": {
        "name": "杭州·全域", "group": "杭州", "scale": "全行政区",
        "center": [119.55, 29.95],
        "bbox": [118.35, 29.18, 120.72, 30.57],
        "camera_height": 120000, "utm_epsg": 32650,
    },
    "tokyo": {
        "name": "东京", "group": "东京", "scale": "样例",
        "center": [139.767, 35.681],
        "bbox": [139.66, 35.62, 139.88, 35.75],
        "camera_height": 12000, "utm_epsg": 32654,
    },
}

# 兼容旧引用名
CITIES = AREAS
DEFAULT_CITY = "hangzhou_core"

# ── 视域分析默认参数 ──
VIEWSHED_DEFAULTS = {
    "eye_height": 30,        # 默认观察高度（米）
    "radius": 600,           # 默认分析半径（米）
    "azimuths": 96,          # 方位采样数
    "pitches": [-8, 0, 8, 18],  # 俯仰角（度）
    "show_area": True,       # 默认显示覆盖区域面
}

# ── 日照默认参数 ──
SUNLIGHT_DEFAULTS = {
    "hour": 9,               # 默认时刻
    "dark_night": True,      # 夜间压暗
    "shadows": True,         # 默认开启阴影
    "shadow_size": 2048,
    "shadow_darkness": 0.45,
    "shadow_max_distance": 5000,
}

# ── 建筑样式配置 ──
BUILDING_STYLES = {
    "white": "#dfe6f2",
    "height_colors": [
        (120, "#ff5c7c"), (80, "#ff9d5c"), (45, "#ffe07c"),
        (20, "#7cf6c8"), (0, "#4da3ff")
    ],
    "highlight": "#ffd24d",
}
