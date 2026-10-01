"""API-key auth. Keys are generated once, shown once (registration response /
seed script output), and stored only as a SHA-256 hash — same 'never store the
plaintext secret' discipline as every prior capstone's handling of tokens.

Authorization model is two-tier, and both tiers are ACTUALLY enforced (not just
'logged in or not'):
  - a regular agent key can create/read/act on its OWN tasks and claims only —
    reading another agent's task is a 403, not a 404 (we don't leak existence
    either way beyond that, to keep this simple, but ownership is checked for
    real on every route that takes a task_id/claim_id)
  - the admin key (separate from any agent) is the only key that can generate
    or list audit reports
"""
import hashlib
import secrets
from datetime import datetime, timezone

from app.config import ADMIN_API_KEY
from app.lib.errors import AuthError, ForbiddenError


def generate_api_key() -> str:
    return "pod_" + secrets.token_urlsafe(32)


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def create_agent(conn, *, name: str) -> tuple[dict, str]:
    """Returns (agent_row, plaintext_key). The plaintext key is NEVER stored —
    this is the only place it ever exists outside the caller's hands."""
    plaintext = generate_api_key()
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        "INSERT INTO agents (name, api_key_hash, is_admin, created_at) VALUES (?, ?, 0, ?)",
        (name, hash_api_key(plaintext), now),
    )
    agent = conn.execute("SELECT * FROM agents WHERE id = ?", (cur.lastrowid,)).fetchone()
    return dict(agent), plaintext


def authenticate_agent(conn, authorization_header: str | None) -> dict:
    """Raises AuthError (401) if the header is missing/malformed/unknown."""
    key = _extract_bearer(authorization_header)
    if key is None:
        raise AuthError("missing or malformed Authorization header")
    row = conn.execute(
        "SELECT * FROM agents WHERE api_key_hash = ?", (hash_api_key(key),)
    ).fetchone()
    if row is None:
        raise AuthError("invalid API key")
    return dict(row)


def authenticate_admin(authorization_header: str | None) -> None:
    """Raises AuthError (401) if missing/wrong. ADMIN_API_KEY is a single
    shared secret (not an agent row) — simplest thing that actually enforces
    a real second tier, deliberately not over-built for a 3-week scope."""
    key = _extract_bearer(authorization_header)
    if key is None or not ADMIN_API_KEY or key != ADMIN_API_KEY:
        raise AuthError("missing or invalid admin API key")


def require_owns_task(conn, agent: dict, task_id: int) -> dict:
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        raise ForbiddenError(f"no task {task_id}")
    if row["agent_id"] != agent["id"]:
        raise ForbiddenError("not your task")
    return dict(row)


def require_owns_claim(conn, agent: dict, claim_id: int) -> dict:
    row = conn.execute("SELECT * FROM claims WHERE id = ?", (claim_id,)).fetchone()
    if row is None:
        raise ForbiddenError(f"no claim {claim_id}")
    if row["agent_id"] != agent["id"]:
        raise ForbiddenError("not your claim")
    return dict(row)


def _extract_bearer(header: str | None) -> str | None:
    if not header:
        return None
    parts = header.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None
