"""GenXAI Workflow Studio backend."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.config import BACKEND_ROOT, get_settings
from app.tools_bootstrap import register_studio_tools

logging.basicConfig(level=logging.INFO)


def seed_examples(data_dir: Path) -> None:
    """Add bundled example workflows that aren't in the store yet."""
    from app.api.routes import get_store
    from app.schemas import WorkflowDoc

    store = get_store()
    seed_dir = BACKEND_ROOT / "seed"
    for example in seed_dir.glob("*.json"):
        try:
            doc = WorkflowDoc.model_validate_json(example.read_text())
        except Exception:
            continue
        if doc.id and store.get(doc.id) is None:
            store.create(doc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.api.routes import get_schedule_manager
    from app.run_manager import get_run_manager

    run_manager = get_run_manager()
    run_manager.recover_stale_runs()
    await run_manager.start()

    schedule_manager = get_schedule_manager()
    await schedule_manager.resume_enabled()

    from app.mcp_registry import sync_agent_tools

    await sync_agent_tools()
    yield
    await schedule_manager.shutdown()
    await run_manager.shutdown()


def create_app() -> FastAPI:
    settings = get_settings()
    from genxai.core.datasets import configure_dataset_store
    from genxai.core.files import configure_file_store

    configure_file_store(settings.data_dir / "files")
    configure_dataset_store(settings.data_dir / "datasets.db")
    register_studio_tools()
    seed_examples(settings.data_dir)

    from app.demo_seed import seed_demo_data

    seed_demo_data()

    app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)

    if settings.studio_api_token:
        from fastapi.responses import JSONResponse

        hooks_prefix = f"{settings.api_prefix}/hooks/"
        # Browser redirect from the OAuth provider: can't carry our header.
        # Safe without it — the single-use state nonce authenticates the request.
        oauth_callback_path = f"{settings.api_prefix}/oauth/callback"

        @app.middleware("http")
        async def require_token(request, call_next):
            path = request.url.path
            needs_auth = (
                path.startswith(settings.api_prefix)
                and not path.startswith(hooks_prefix)
                and path != oauth_callback_path
                and request.method != "OPTIONS"
            )
            if needs_auth and request.headers.get("X-Studio-Token") != settings.studio_api_token:
                return JSONResponse({"detail": "Missing or invalid X-Studio-Token"}, status_code=401)
            return await call_next(request)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[origin.strip() for origin in settings.cors_origins.split(",")],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router, prefix=settings.api_prefix)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "app": settings.app_name}

    from fastapi import Response

    from genxai.observability.metrics import get_prometheus_metrics

    @app.get("/metrics", response_class=Response)
    def metrics() -> Response:
        """Prometheus metrics for every workflow/node/agent/tool execution.

        The graph engine records these globally as it runs (no Studio-side
        instrumentation needed); this just exposes the same collector the
        framework already ships (`genxai.observability.metrics`). Install
        the optional `prometheus_client` dependency for real values —
        without it this returns a placeholder line.
        """
        return Response(content=get_prometheus_metrics(), media_type="text/plain")

    return app


app = create_app()
