"""The cron-style half of the 'background or cron jobs' concept, distinct
from the queue-draining workers in verification.py/llm_review.py: a sweep
that runs on a schedule and flags claims that sat unresolved too long. Per
the brief's own glossary — 'a task that runs at fixed times... no request
involved' — this is meant to be invoked by an actual OS scheduler (cron /
Windows Task Scheduler) hitting POST /v1/sweep-runs, not by a user action."""
from datetime import datetime, timedelta, timezone

from app.config import SLA_SECONDS
from app.db import get_connection

_UNRESOLVED_STATUSES = ("pending", "verifying", "needs_llm_review", "reviewing")


def sweep_stale_claims(db_path: str, *, now: str | None = None, sla_seconds: int | None = None) -> dict:
    conn = get_connection(db_path)
    try:
        now_iso = now or datetime.now(timezone.utc).isoformat()
        sla = sla_seconds if sla_seconds is not None else SLA_SECONDS
        cutoff = (datetime.fromisoformat(now_iso) - timedelta(seconds=sla)).isoformat()

        placeholders = ",".join("?" for _ in _UNRESOLVED_STATUSES)
        stale_rows = conn.execute(
            f"SELECT id FROM claims WHERE status IN ({placeholders}) AND created_at < ?",
            (*_UNRESOLVED_STATUSES, cutoff),
        ).fetchall()

        flagged = 0
        for row in stale_rows:
            claim_id = row["id"]
            conn.execute(
                """INSERT INTO verification_attempts (claim_id, method, outcome, detail, checked_at)
                   VALUES (?, 'sla_sweep', 'fail', ?, ?)""",
                (claim_id, f"unresolved for over {sla}s - auto-flagged by the SLA sweep", now_iso),
            )
            conn.execute(
                "UPDATE claims SET status='suspicious', updated_at=?, resolved_at=? WHERE id=?",
                (now_iso, now_iso, claim_id),
            )
            flagged += 1
        conn.commit()

        return {"swept_at": now_iso, "sla_seconds": sla, "flagged": flagged}
    finally:
        conn.close()
