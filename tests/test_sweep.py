"""The cron-style half of the background/cron concept — distinct from the
queue-draining workers in verification.py/llm_review.py. Meant to be hit by
an OS scheduler on a fixed interval, so these tests drive it with an explicit
`now` rather than real wall-clock sleeps."""
from datetime import datetime, timedelta, timezone

from app.lib.claims import create_claim, get_claim
from app.lib.sweep import sweep_stale_claims


NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


def _claim(conn, agent, task, created_at=None, status="pending"):
    c = create_claim(conn, task_id=task["id"], agent_id=agent["id"], claim_text="x",
                      evidence_type="text", evidence_payload={})
    if created_at or status != "pending":
        conn.execute(
            "UPDATE claims SET status=?, created_at=? WHERE id=?",
            (status, created_at or c["created_at"], c["id"]),
        )
        conn.commit()
    return get_claim(conn, c["id"])


def test_sweep_flags_claims_unresolved_past_the_sla(conn, db_path, make_agent, make_task):
    agent, _ = make_agent()
    task = make_task(agent["id"])
    old = (NOW - timedelta(seconds=600)).isoformat()
    stale = _claim(conn, agent, task, created_at=old, status="pending")

    result = sweep_stale_claims(db_path, now=NOW.isoformat(), sla_seconds=300)

    assert result == {"swept_at": NOW.isoformat(), "sla_seconds": 300, "flagged": 1}
    assert get_claim(conn, stale["id"])["status"] == "suspicious"


def test_sweep_leaves_fresh_claims_alone(conn, db_path, make_agent, make_task):
    agent, _ = make_agent()
    task = make_task(agent["id"])
    recent = (NOW - timedelta(seconds=60)).isoformat()
    fresh = _claim(conn, agent, task, created_at=recent, status="pending")

    result = sweep_stale_claims(db_path, now=NOW.isoformat(), sla_seconds=300)

    assert result["flagged"] == 0
    assert get_claim(conn, fresh["id"])["status"] == "pending"


def test_sweep_leaves_already_resolved_claims_alone(conn, db_path, make_agent, make_task):
    agent, _ = make_agent()
    task = make_task(agent["id"])
    old = (NOW - timedelta(seconds=600)).isoformat()
    verified = _claim(conn, agent, task, created_at=old, status="verified")

    result = sweep_stale_claims(db_path, now=NOW.isoformat(), sla_seconds=300)

    assert result["flagged"] == 0
    assert get_claim(conn, verified["id"])["status"] == "verified"


def test_sweep_catches_every_unresolved_status_not_just_pending(conn, db_path, make_agent, make_task):
    agent, _ = make_agent()
    task = make_task(agent["id"])
    old = (NOW - timedelta(seconds=600)).isoformat()
    for status in ("pending", "verifying", "needs_llm_review", "reviewing"):
        _claim(conn, agent, task, created_at=old, status=status)

    result = sweep_stale_claims(db_path, now=NOW.isoformat(), sla_seconds=300)

    assert result["flagged"] == 4


def test_sweep_logs_a_verification_attempt_explaining_why(conn, db_path, make_agent, make_task):
    agent, _ = make_agent()
    task = make_task(agent["id"])
    old = (NOW - timedelta(seconds=600)).isoformat()
    stale = _claim(conn, agent, task, created_at=old, status="pending")

    sweep_stale_claims(db_path, now=NOW.isoformat(), sla_seconds=300)

    attempt = conn.execute(
        "SELECT * FROM verification_attempts WHERE claim_id=?", (stale["id"],)
    ).fetchone()
    assert attempt["method"] == "sla_sweep"
    assert attempt["outcome"] == "fail"
    assert "300s" in attempt["detail"]
