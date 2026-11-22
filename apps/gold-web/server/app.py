from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path as FilePath
from typing import Any, Callable

from fastapi import Depends, FastAPI, HTTPException, Path, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import Settings, get_settings
from .repository import GoldRepository, GoldWebError, GoldNotReady
from .schemas import FilterParams


PUBLIC_SCOPE = "nguồn cung tin hệ thống quan sát được"
SALARY_DEFINITION = "Trung điểm khoảng lương VND trên các tin có đủ min/max hợp lệ"


def create_app(
    *,
    settings: Settings | None = None,
    repository_factory: Callable[[], Any] | None = None,
) -> FastAPI:
    settings = settings or get_settings()

    def default_factory() -> Any:
        # Demo is an explicit development mode, never a fallback for a missing or
        # broken Gold connection. Production rejects demo in Settings.
        if settings.demo_mode:
            from .demo import DemoRepository

            return DemoRepository(settings)
        return GoldRepository(settings)

    factory = repository_factory or default_factory
    app = FastAPI(
        title="IT Insights Gold API",
        version="0.1.0",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
    )

    @app.middleware("http")
    async def no_store_api_responses(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def repository() -> Any:
        return factory()

    def meta(context, *, mode: str | None = None) -> dict[str, object]:
        served_at = datetime.now(timezone.utc)
        age_days = GoldRepository._age_days(context.snapshot_date)
        return {
            "gold_release_id": context.gold_release_id,
            "silver_release_id": context.silver_release_id,
            "snapshot_date": context.snapshot_date,
            "snapshot_cutoff_at": context.snapshot_cutoff_at,
            "switched_at": context.switched_at,
            "served_at": served_at.isoformat(),
            "mode": mode or settings.mode,
            "age_days": age_days,
            "stale_after_days": settings.stale_after_days,
            "stale": age_days > settings.stale_after_days,
            "scope": PUBLIC_SCOPE,
            "salary_definition": SALARY_DEFINITION,
        }

    def with_meta(repo: GoldRepository, payload: dict, params: FilterParams | None = None) -> dict:
        payload = dict(payload)
        context = payload.pop("_context", None) or repo.release_context()
        if params is not None:
            repo._assert_expected_release(context, params)
        return {"meta": meta(context), **payload}

    @app.exception_handler(GoldWebError)
    async def gold_error_handler(_: Request, exc: GoldWebError) -> JSONResponse:
        code = getattr(exc, "code", "GOLD_UNAVAILABLE")
        message = getattr(exc, "public_message", "Chưa thể đọc dữ liệu Gold.")
        response_status = status.HTTP_503_SERVICE_UNAVAILABLE
        if code == "RELEASE_CHANGED":
            response_status = status.HTTP_409_CONFLICT
        return JSONResponse(
            status_code=response_status,
            content={"error": {"code": code, "message": message}},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "INVALID_REQUEST", "message": "Tham số yêu cầu không hợp lệ."}},
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        if isinstance(exc.detail, dict) and "code" in exc.detail:
            detail = exc.detail
        else:
            code = "NOT_FOUND" if exc.status_code == status.HTTP_404_NOT_FOUND else "HTTP_ERROR"
            detail = {"code": code, "message": str(exc.detail)}
        return JSONResponse(status_code=exc.status_code, content={"error": detail})

    @app.get("/api/health")
    def health() -> dict[str, object]:
        return {
            "status": "ok",
            "service": "it-insight-web",
            "mode": settings.mode,
            "ai_enabled": False,
        }

    @app.get("/api/ready")
    def ready(repo: GoldRepository = Depends(repository)) -> dict[str, object]:
        context = repo.ready()
        return {"status": "ready", "meta": meta(context)}

    @app.get("/api/filters")
    def filters(
        params: FilterParams = Depends(),
        repo: GoldRepository = Depends(repository),
    ) -> dict[str, object]:
        return with_meta(repo, repo.filters(), params)

    @app.get("/api/dashboard")
    def dashboard(
        params: FilterParams = Depends(),
        repo: GoldRepository = Depends(repository),
    ) -> dict[str, object]:
        return with_meta(repo, repo.dashboard(params), params)

    @app.get("/api/jobs")
    def jobs(
        params: FilterParams = Depends(),
        repo: GoldRepository = Depends(repository),
    ) -> dict[str, object]:
        return with_meta(repo, repo.jobs(params), params)

    @app.get("/api/jobs/{job_id}")
    def job_detail(job_id: str = Path(min_length=1, max_length=200), repo: GoldRepository = Depends(repository)) -> dict[str, object]:
        payload = repo.job_detail(job_id)
        if payload is None:
            raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Không tìm thấy tin trong release hiện hành."})
        return with_meta(repo, payload)

    @app.get("/api/skills")
    def skills(
        params: FilterParams = Depends(),
        repo: GoldRepository = Depends(repository),
    ) -> dict[str, object]:
        return with_meta(repo, repo.skills(params), params)

    @app.get("/api/companies")
    def companies(
        params: FilterParams = Depends(),
        repo: GoldRepository = Depends(repository),
    ) -> dict[str, object]:
        return with_meta(repo, repo.companies(params), params)

    @app.get("/api/quality")
    def quality(
        params: FilterParams = Depends(),
        repo: GoldRepository = Depends(repository),
    ) -> dict[str, object]:
        return with_meta(repo, repo.quality(params), params)

    @app.get("/api/assistant/status")
    def assistant_status() -> dict[str, object]:
        return {
            "enabled": False,
            "status": "not_configured",
            "message": "Trợ lý AI chưa được cấu hình. Web tra cứu Gold vẫn hoạt động.",
            "examples": [
                "So sánh số tin theo địa điểm",
                "Tìm kỹ năng thường được nhắc đến cùng Python",
            ],
        }

    static_dir = FilePath(__file__).with_name("static")
    if static_dir.exists():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="frontend")

    # Uptime monitors commonly probe with HEAD; Starlette only auto-handles HEAD
    # when the route declares it. Advertise HEAD wherever GET exists so probes see
    # the same status code without a body.
    for route in app.routes:
        if isinstance(route, APIRoute) and "GET" in route.methods:
            route.methods.add("HEAD")
    return app


app = create_app()
