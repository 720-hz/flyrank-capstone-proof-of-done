"""The durable verification worker. Same shape as the social-studio capstone's
publish worker: claiming a claim is a conditional UPDATE (`WHERE status='pending'`),
committed immediately and independently of actually checking it, so a crash
between 'claimed' and 'resolved' leaves it reclaimable rather than silently
re-checked twice or lost. A 'verifying' claim older than CLAIM_TIMEOUT_SECONDS
is treated as abandoned by a dead worker and reclaimed by the next run.

Routing is the whole point of this file: 'file' and 'url' evidence get a real
deterministic check right here; 'text' always, and 'command' usually, can't be
checked deterministically at all, so they're routed to 'needs_llm_review'
instead of being silently trusted or silently rejected."""
import json
from datetime import datetime, timedelta, timezone

from app.config import CLAIM_TIMEOUT_SECONDS
from app.db import get_connection
from app.lib.evidence_checks import check_file_evidence, check_url_evidence


def _reclaim_stale(conn, now_iso: str, timeout_seconds: int) -> int:
    cutoff = (datetime.fromisoformat(now_iso) - timedelta(seconds=timeout_seconds)).isoformat()
    cur = conn.execute(
        "UPDATE claims SET status='pending', claimed_at=NULL "
        "WHERE status='verifying' AND claimed_at < ?",
        (cutoff,),
    )
    return cur.rowcount


def _due_pending_claim_ids(conn, limit: int) -> list[int]:
    rows = conn.execute(
        "SELECT id FROM claims WHERE status='pending' ORDER BY id LIMIT ?", (limit,)
    ).fetchall()
    return [r["id"] for r in rows]


def _claim_one(conn, claim_id: int, now_iso: str) -> bool:
    cur = conn.execute(
        "UPDATE claims SET status='verifying', claimed_at=? WHERE id=? AND status='pending'",
        (now_iso, claim_id),
    )
    return cur.rowcount == 1


def _log_attempt(conn, claim_id: int, method: str, outcome: str, detail: str, now_iso: str):
    conn.execute(
        """INSERT INTO verification_attempts (claim_id, method, outcome, detail, checked_at)
           VALUES (?, ?, ?, ?, ?)""",
        (claim_id, method, outcome, detail, now_iso),
    )


def _resolve(conn, claim_id: int, new_status: str, now_iso: str, *, terminal: bool):
    conn.execute(
        "UPDATE claims SET status=?, updated_at=?, resolved_at=? WHERE id=?",
        (new_status, now_iso, now_iso if terminal else None, claim_id),
    )


def _verify_one(conn, claim_id: int, now_iso: str, *, url_client=None):
    row = conn.execute("SELECT * FROM claims WHERE id = ?", (claim_id,)).fetchone()
    evidence_type = row["evidence_type"]
    payload = json.loads(row["evidence_payload"])

    if evidence_type == "file":
        outcome, detail = check_file_evidence(payload)
        _log_attempt(conn, claim_id, "file_hash", outcome, detail, now_iso)
        new_status = "verified" if outcome == "pass" else "failed"
        _resolve(conn, claim_id, new_status, now_iso, terminal=True)
        return new_status

    if evidence_type == "url":
        outcome, detail = check_url_evidence(payload, client=url_client)
        _log_attempt(conn, claim_id, "url_check", outcome, detail, now_iso)
        new_status = "verified" if outcome == "pass" else "failed"
        _resolve(conn, claim_id, new_status, now_iso, terminal=True)
        return new_status

    if evidence_type == "command":
        log_excerpt = payload.get("log_excerpt")
        if not log_excerpt:
            detail = (
                "command evidence had no log_excerpt - a self-reported exit "
                "code with no supporting output is not evidence on its own"
            )
            _log_attempt(conn, claim_id, "llm_review", "fail", detail, now_iso)
            _resolve(conn, claim_id, "suspicious", now_iso, terminal=True)
            return "suspicious"
        detail = (
            "command evidence is self-reported and cannot be independently "
            "verified by the server - routed to LLM review of the log excerpt"
        )
        _log_attempt(conn, claim_id, "llm_review", "inconclusive", detail, now_iso)
        _resolve(conn, claim_id, "needs_llm_review", now_iso, terminal=False)
        return "needs_llm_review"

    # evidence_type == "text": no artifact exists to check deterministically at all
    detail = "text evidence has no checkable artifact - routed to LLM review"
    _log_attempt(conn, claim_id, "llm_review", "inconclusive", detail, now_iso)
    _resolve(conn, claim_id, "needs_llm_review", now_iso, terminal=False)
    return "needs_llm_review"


def run_verification_batch(db_path: str, *, now: str | None = None, limit: int = 50, url_client=None) -> dict:
    conn = get_connection(db_path)
    try:
        now_iso = now or datetime.now(timezone.utc).isoformat()

        _reclaim_stale(conn, now_iso, CLAIM_TIMEOUT_SECONDS)
        conn.commit()

        cur = conn.execute(
            "INSERT INTO claim_jobs (status, total, processed, started_at) VALUES ('running', 0, 0, ?)",
            (now_iso,),
        )
        job_id = cur.lastrowid
        conn.commit()

        due_ids = _due_pending_claim_ids(conn, limit)
        conn.execute("UPDATE claim_jobs SET total=? WHERE id=?", (len(due_ids), job_id))
        conn.commit()

        processed = 0
        last_error = None
        for claim_id in due_ids:
            if not _claim_one(conn, claim_id, now_iso):
                conn.commit()
                continue
            conn.commit()

            try:
                _verify_one(conn, claim_id, now_iso, url_client=url_client)
                conn.commit()
            except Exception as exc:  # noqa: BLE001
                conn.rollback()
                last_error = str(exc)

            processed += 1
            conn.execute("UPDATE claim_jobs SET processed=? WHERE id=?", (processed, job_id))
            conn.commit()

        conn.execute(
            "UPDATE claim_jobs SET status='completed', finished_at=?, last_error=? WHERE id=?",
            (datetime.now(timezone.utc).isoformat(), last_error, job_id),
        )
        conn.commit()

        return {"job_id": job_id, "total": len(due_ids), "processed": processed}
    finally:
        conn.close()
