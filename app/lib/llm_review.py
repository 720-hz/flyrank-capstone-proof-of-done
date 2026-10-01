"""The second durable worker, same claim/reclaim shape as verification.py,
operating on claims already routed to 'needs_llm_review'. Separated from
verification.py on purpose: the deterministic checks and the LLM call are
different failure modes (a bad file path vs. a slow/failing API call) and
keeping them as two small workers, each with its own claim step, means a
timeout in one never blocks the other."""
import json
from datetime import datetime, timedelta, timezone

from app.config import CLAIM_TIMEOUT_SECONDS
from app.db import get_connection
from app.judges.registry import get_judge

# plausible needs at least this much confidence to be trusted outright —
# anything else (suspicious, insufficient_evidence, or low-confidence
# plausible) is conservatively flagged rather than silently accepted
_VERIFIED_CONFIDENCE_THRESHOLD = 0.6


def _reclaim_stale(conn, now_iso: str, timeout_seconds: int) -> int:
    cutoff = (datetime.fromisoformat(now_iso) - timedelta(seconds=timeout_seconds)).isoformat()
    cur = conn.execute(
        "UPDATE claims SET status='needs_llm_review', claimed_at=NULL "
        "WHERE status='reviewing' AND claimed_at < ?",
        (cutoff,),
    )
    return cur.rowcount


def _due_claim_ids(conn, limit: int) -> list[int]:
    rows = conn.execute(
        "SELECT id FROM claims WHERE status='needs_llm_review' ORDER BY id LIMIT ?", (limit,)
    ).fetchall()
    return [r["id"] for r in rows]


def _claim_one(conn, claim_id: int, now_iso: str) -> bool:
    cur = conn.execute(
        "UPDATE claims SET status='reviewing', claimed_at=? WHERE id=? AND status='needs_llm_review'",
        (now_iso, claim_id),
    )
    return cur.rowcount == 1


def _review_one(conn, claim_id: int, now_iso: str, judge) -> str:
    row = conn.execute(
        """SELECT c.*, t.description AS task_description
           FROM claims c JOIN tasks t ON t.id = c.task_id
           WHERE c.id = ?""",
        (claim_id,),
    ).fetchone()
    evidence_payload = json.loads(row["evidence_payload"])

    result = judge.judge(
        task_description=row["task_description"],
        claim_text=row["claim_text"],
        evidence_type=row["evidence_type"],
        evidence_payload=evidence_payload,
    )

    conn.execute(
        """INSERT INTO llm_reviews
               (claim_id, provider, model, verdict, confidence, reasoning,
                input_tokens, output_tokens, cost_micro_cents, raw_response, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            claim_id, judge.provider, judge.model, result["verdict"], result["confidence"],
            result["reasoning"], result["input_tokens"], result["output_tokens"],
            result["cost_micro_cents"], json.dumps(result["raw_response"]), now_iso,
        ),
    )

    outcome = (
        "pass" if result["verdict"] == "plausible" and result["confidence"] >= _VERIFIED_CONFIDENCE_THRESHOLD
        else "fail" if result["verdict"] == "suspicious"
        else "inconclusive"
    )
    conn.execute(
        """INSERT INTO verification_attempts (claim_id, method, outcome, detail, checked_at)
           VALUES (?, 'llm_review', ?, ?, ?)""",
        (claim_id, outcome, f"{judge.provider}/{judge.model}: {result['reasoning']}", now_iso),
    )

    new_status = (
        "verified" if result["verdict"] == "plausible" and result["confidence"] >= _VERIFIED_CONFIDENCE_THRESHOLD
        else "suspicious"
    )
    conn.execute(
        "UPDATE claims SET status=?, updated_at=?, resolved_at=? WHERE id=?",
        (new_status, now_iso, now_iso, claim_id),
    )
    return new_status


def run_llm_review_batch(db_path: str, *, now: str | None = None, limit: int = 50, judge=None) -> dict:
    conn = get_connection(db_path)
    try:
        now_iso = now or datetime.now(timezone.utc).isoformat()
        active_judge = get_judge(judge)

        _reclaim_stale(conn, now_iso, CLAIM_TIMEOUT_SECONDS)
        conn.commit()

        cur = conn.execute(
            "INSERT INTO claim_jobs (status, total, processed, started_at) VALUES ('running', 0, 0, ?)",
            (now_iso,),
        )
        job_id = cur.lastrowid
        conn.commit()

        due_ids = _due_claim_ids(conn, limit)
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
                _review_one(conn, claim_id, now_iso, active_judge)
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
