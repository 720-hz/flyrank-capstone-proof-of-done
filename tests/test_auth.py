"""Agent registration, API key hashing, and the two-tier authorization model
(auth.py) — the 'Authentication' concept. Ownership enforcement (require_owns_*)
is tested here at the lib level; 401/403 over real HTTP is tested separately
in test_api.py, since that's the layer deps.py adds on top of this one."""
import pytest

from app.lib.auth import (
    authenticate_admin,
    authenticate_agent,
    create_agent,
    generate_api_key,
    hash_api_key,
    require_owns_claim,
    require_owns_task,
)
from app.lib.errors import AuthError, ForbiddenError


def test_generate_api_key_is_unique_and_prefixed():
    a, b = generate_api_key(), generate_api_key()
    assert a != b
    assert a.startswith("pod_")
    assert b.startswith("pod_")


def test_hash_api_key_is_deterministic_and_one_way():
    key = "pod_abc123"
    h1, h2 = hash_api_key(key), hash_api_key(key)
    assert h1 == h2
    assert h1 != key
    assert len(h1) == 64  # sha256 hex digest


def test_create_agent_never_stores_plaintext(conn):
    agent, plaintext = create_agent(conn, name="alice")
    assert agent["name"] == "alice"
    assert plaintext.startswith("pod_")
    # the stored row only has the hash, and it's not reversible to the plaintext
    assert agent["api_key_hash"] == hash_api_key(plaintext)
    assert "api_key" not in agent
    row = conn.execute("SELECT * FROM agents WHERE id = ?", (agent["id"],)).fetchone()
    assert plaintext not in dict(row).values()


def test_authenticate_agent_accepts_its_own_key(conn, make_agent):
    agent, plaintext = make_agent("bob")
    authenticated = authenticate_agent(conn, f"Bearer {plaintext}")
    assert authenticated["id"] == agent["id"]


def test_authenticate_agent_rejects_unknown_key(conn):
    with pytest.raises(AuthError):
        authenticate_agent(conn, "Bearer pod_not-a-real-key")


@pytest.mark.parametrize("header", [None, "", "not-bearer-format", "Bearer", "Basic abc123"])
def test_authenticate_agent_rejects_malformed_headers(conn, header):
    with pytest.raises(AuthError):
        authenticate_agent(conn, header)


def test_authenticate_admin_accepts_the_configured_key(monkeypatch):
    monkeypatch.setattr("app.lib.auth.ADMIN_API_KEY", "shh-its-a-secret")
    authenticate_admin("Bearer shh-its-a-secret")  # does not raise


def test_authenticate_admin_rejects_wrong_or_missing_key(monkeypatch):
    monkeypatch.setattr("app.lib.auth.ADMIN_API_KEY", "shh-its-a-secret")
    with pytest.raises(AuthError):
        authenticate_admin("Bearer wrong-key")
    with pytest.raises(AuthError):
        authenticate_admin(None)


def test_authenticate_admin_rejects_everything_when_unconfigured(monkeypatch):
    # an empty ADMIN_API_KEY (unset in .env) must never match an empty/blank
    # bearer token — otherwise an unconfigured deployment would silently accept
    # admin requests from anyone.
    monkeypatch.setattr("app.lib.auth.ADMIN_API_KEY", "")
    with pytest.raises(AuthError):
        authenticate_admin("Bearer ")
    with pytest.raises(AuthError):
        authenticate_admin(None)


def test_require_owns_task_allows_the_owner(conn, make_agent, make_task):
    agent, _ = make_agent()
    task = make_task(agent["id"])
    assert require_owns_task(conn, agent, task["id"])["id"] == task["id"]


def test_require_owns_task_forbids_another_agent(conn, make_agent, make_task):
    owner, _ = make_agent("owner")
    stranger, _ = make_agent("stranger")
    task = make_task(owner["id"])
    with pytest.raises(ForbiddenError):
        require_owns_task(conn, stranger, task["id"])


def test_require_owns_task_forbids_nonexistent_task(conn, make_agent):
    agent, _ = make_agent()
    with pytest.raises(ForbiddenError):
        require_owns_task(conn, agent, 999999)


def test_require_owns_claim_forbids_another_agent(conn, make_agent, make_task):
    from app.lib.claims import create_claim

    owner, _ = make_agent("owner")
    stranger, _ = make_agent("stranger")
    task = make_task(owner["id"])
    claim = create_claim(
        conn, task_id=task["id"], agent_id=owner["id"], claim_text="did it",
        evidence_type="text", evidence_payload={},
    )
    with pytest.raises(ForbiddenError):
        require_owns_claim(conn, stranger, claim["id"])
