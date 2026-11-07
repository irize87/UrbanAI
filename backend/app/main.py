from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.core.config import ROOT_DIR, get_settings
from app.db.fixtures import load_fixtures
from app.db.schema import connect, init_db


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    with connect() as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM buildings").fetchone()["c"]
    if count == 0:
        load_fixtures()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="sitedia (Seocho)",
        description="서초구 한정 대지분석 다이어그램 API",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_origin_regex=(
            r"https?://(localhost|127\.0\.0\.1|0\.0\.0\.0|"
            r"192\.168\.\d{1,3}\.\d{1,3}|"
            r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
            r"172\.(1[6-9]|2\d|3[0-1])\.\d{1,3}\.\d{1,3}"
            r")(:\d+)?$"
        ),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    dist_dir = ROOT_DIR / "frontend" / "dist"
    if dist_dir.is_dir():
        # API 라우트를 먼저 등록한 뒤 정적 파일을 붙인다. /api 는 여기서 가로채지 않는다.
        app.mount("/", StaticFiles(directory=dist_dir, html=True), name="frontend")
    return app


app = create_app()
