"""Shared fixtures.

Two layers of tests live here, same split as every prior FlyRank capstone:
  - lib-level tests call app/lib/*.py and app/judges/*.py functions directly
    against a throwaway sqlite file — this is the bulk of the suite, and it
    exercises the exact same functions the live API calls.
  - a smaller set of true HTTP-level tests drive the FastAPI app through
    TestClient, specifically to prove routing + the two-tier auth is wired
    up correctly end to end (401/403 enforcement can't be proven by calling
    lib functions directly, since that's precisely the layer deps.py adds).

DB_PATH is read from app.config at import time and copied into every module
that does `from app.config import DB_PATH`, so an HTTP-level test can't just
set an env var after import — the `api_client` fixture monkeypatches every
module's already-imported copy to point at a fresh per-test sqlite file.
"""
import os

import pytest

from app.db import get_connection, init_db


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "test.db")
    init_db(path)
    return path


@pytest.fixture
def conn(db_path):
    connection = get_connection(db_path)
    yield connection
    connection.close()


@pytest.fixture
def make_agent(conn):
    """Factory: make_agent(name="x") -> (agent_dict, plaintext_key)."""
    from app.lib.auth import create_agent

    def _make(name: str = "test-agent"):
        return create_agent(conn, name=name)

    return _make


@pytest.fixture
def make_task(conn):
    """Factory: make_task(agent_id, title=..., description=...) -> task_dict."""
    from app.lib.tasks import create_task

    def _make(agent_id: int, title: str = "do a thing", description: str = "a thing worth doing"):
        return create_task(conn, agent_id=agent_id, title=title, description=description)

    return _make


@pytest.fixture
def admin_key(monkeypatch):
    """Patches the single ADMIN_API_KEY shared secret everywhere it's already
    been imported (just app.lib.auth, since every other module calls through
    authenticate_admin() rather than reading the constant itself)."""
    key = "test-admin-key-12345"
    monkeypatch.setattr("app.lib.auth.ADMIN_API_KEY", key)
    return key


@pytest.fixture
def api_client(db_path, admin_key, monkeypatch):
    """A FastAPI TestClient wired to a fresh per-test sqlite file and a known
    admin key, for HTTP-level auth/routing tests."""
    from fastapi.testclient import TestClient

    import app.db as db_module
    import app.deps as deps_module
    import app.lib.reports as reports_lib_module
    import app.routes.agents as agents_module
    import app.routes.claims as claims_module
    import app.routes.reports as reports_module
    import app.routes.sweep as sweep_module
    import app.routes.tasks as tasks_module
    from app.main import app

    for module in (
        db_module, deps_module, agents_module, claims_module,
        reports_module, sweep_module, tasks_module,
    ):
        monkeypatch.setattr(module, "DB_PATH", db_path)

    # reports.py writes PDFs under a module-level REPORTS_DIR computed at
    # import time (os.path.abspath("./reports")); redirect it into this
    # test's own tmp_path so test runs never touch the real repo directory.
    monkeypatch.setattr(reports_lib_module, "REPORTS_DIR", os.path.join(os.path.dirname(db_path), "reports"))

    with TestClient(app) as client:
        yield client


@pytest.fixture
def register_via_api(api_client):
    """Factory: register_via_api(name="x") -> (agent_json, api_key, auth_headers)."""

    def _register(name: str = "test-agent"):
        resp = api_client.post("/v1/agents", json={"name": name})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        headers = {"Authorization": f"Bearer {body['api_key']}"}
        return body, body["api_key"], headers

    return _register
