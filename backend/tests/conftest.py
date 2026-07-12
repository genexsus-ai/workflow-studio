"""Test fixtures for the Workflow Studio backend."""

import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parents[2]
for path in (str(BACKEND_ROOT), str(REPO_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)


@pytest.fixture
def client(tmp_path, monkeypatch):
    """FastAPI TestClient with isolated data directory."""
    from fastapi.testclient import TestClient

    import app.api.routes as routes
    import app.runner as runner
    from app.config import get_settings
    from app.main import create_app

    get_settings.cache_clear()
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    settings = get_settings()
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(routes, "_store", None)
    monkeypatch.setattr(runner, "_execution_store", None)

    import app.credentials as credentials

    monkeypatch.setattr(credentials, "_store", None)

    import app.oauth_flow as oauth_flow

    oauth_flow.reset_pending()

    from genxai.core.datasets import reset_dataset_store
    from genxai.core.files import reset_file_store

    reset_file_store()
    reset_dataset_store()

    import app.mcp_registry as mcp_registry

    monkeypatch.setattr(mcp_registry, "_store", None)

    import app.run_manager as run_manager_module

    monkeypatch.setattr(run_manager_module, "_manager", None)

    import app.generation as generation

    monkeypatch.setattr(generation, "_memory", None)

    with TestClient(create_app()) as test_client:
        yield test_client

    get_settings.cache_clear()


@pytest.fixture
def mock_llm(monkeypatch):
    """Patch the LLM factory so agent nodes run without real API calls."""
    from genxai.llm.base import LLMProvider, LLMResponse
    from genxai.llm.factory import LLMProviderFactory

    class StubProvider(LLMProvider):
        def __init__(self, model: str = "stub", **kwargs):
            super().__init__(model=model, temperature=0.0, max_tokens=None)

        async def generate(self, prompt, system_prompt=None, **kwargs):
            return LLMResponse(
                content="stub response",
                model=self.model,
                usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                finish_reason="stop",
            )

        async def generate_stream(self, prompt, system_prompt=None, **kwargs):
            yield "stub response"

        async def generate_chat(self, messages, **kwargs):
            return await self.generate("chat")

    def _create(*args, **kwargs):
        return StubProvider(model=kwargs.get("model", "stub"))

    monkeypatch.setattr(LLMProviderFactory, "create_provider", _create)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    return StubProvider
