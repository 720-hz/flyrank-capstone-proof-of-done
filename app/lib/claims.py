import json
from datetime import datetime, timezone

from app.config import EVIDENCE_TYPES
from app.lib.errors import ValidationError


def create_claim(
    conn, *, task_id: int, agent_id: int, claim_text: str,
    evidence_type: str, evidence_payload: dict,
) -> dict:
    if evidence_type not in EVIDENCE_TYPES:
        raise ValidationError(f"unknown evidence_type {evidence_type!r}")
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        """INSERT INTO claims
               (task_id, agent_id, claim_text, evidence_type, evidence_payload,
                status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)""",
        (task_id, agent_id, claim_text, evidence_type, json.dumps(evidence_payload), now, now),
    )
    return get_claim(conn, cur.lastrowid)


def get_claim(conn, claim_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM claims WHERE id = ?", (claim_id,)).fetchone()
    if row is None:
        return None
    c = dict(row)
    c["evidence_payload"] = json.loads(c["evidence_payload"])
    return c


def list_claims(conn, *, agent_id: int | None = None, status: str | None = None) -> list[dict]:
    query = "SELECT id FROM claims WHERE 1=1"
    params: list = []
    if agent_id is not None:
        query += " AND agent_id = ?"
        params.append(agent_id)
    if status is not None:
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY id DESC"
    rows = conn.execute(query, params).fetchall()
    return [get_claim(conn, r["id"]) for r in rows]


def list_attempts(conn, claim_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM verification_attempts WHERE claim_id = ? ORDER BY id", (claim_id,)
    ).fetchall()
    return [dict(r) for r in rows]
