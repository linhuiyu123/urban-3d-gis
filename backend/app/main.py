"""
FastAPI 应用入口。

启动：uvicorn app.main:app --reload --port 8000
文档：http://localhost:8000/docs
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .routers.api import router

app = FastAPI(
    title="城市三维可视化与分析平台 API",
    description="基于 GeoPandas / NetworkX / PySAL 的空间分析后端，并接入 DeepSeek 实现自然语言分析。",
    version="1.0.0",
)

# 允许前端（Vite 默认 5173 端口）跨域访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # 课程演示用，生产环境应收紧
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/")
def root():
    return {
        "app": "城市三维可视化与分析平台",
        "docs": "/docs",
        "ai_engine": "deepseek" if settings.deepseek_api_key else "mock(未配置DEEPSEEK_API_KEY)",
    }


@app.get("/health")
def health():
    return {"status": "ok"}
