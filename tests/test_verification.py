"""The durable verification worker: evidence-type routing (the design
centerpiece) and the claim/reclaim pattern that makes it crash-safe."""
from datetime import datetime, timedelta, timezone

import httpx
import pytest

import app.lib.evidence_checks as evidence_checks
from app.lib.claims import create_claim, get_claim
from app.lib.verification import _claim_one, run_verification_batch


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    root.mkdir()
    monkeypatch.setattr(evidence_checks, "WORKSPACE_ROOT", str(root))
    return root


@pytest.fixture
def agent_and_task(conn, make_agent, make_task):
    agent, _ = make_agent()
    task = make_task(agent["id"])
    return agent, task


def _make_claim(conn, agent, task, evidence_type, payload, text="did it"):
    return create_claim(
        conn, task_id=task["id"], agent_id=agent["id"], claim_text=text,
        evidence_type=evidence_type, evidence_payload=payload,
    )


def test_file_evidence_routes_to_verified_on_real_file(conn, db_path, workspace, agent_and_task):
    agent, task = agent_and_task
    (workspace / "out.txt").write_text("hi")
    claim = _make_claim(conn, agent, task, "file", {"path": "out.txt"})
    conn.commit()

    result = run_verification_batch(db_path)
    assert result == {"job_id": 1, "total": 1, "processed": 1}
    resolved = get_claim(conn, claim["id"])
    assert resolved["status"] == "verified"
    assert resolved["resolved_at"] is not None


def test_file_evidence_routes_to_failed_when_file_missing(conn, db_path, workspace, agent_and_task):
    agent, task = agent_and_task
    claim = _make_claim(conn, agent, task, "file", {"path": "ghost.txt"})
    conn.commit()

    run_verification_batch(db_path)
    assert get_claim(conn, claim["id"])["status"] == "failed"


def test_url_evidence_uses_injected_client_not_real_network(conn, db_path, agent_and_task):
    agent, task = agent_and_task
    claim = _make_claim(conn, agent, task, "url", {"url": "https://example.com/ok"})
    conn.commit()

    def handler(request):
        return httpx.Response(200)

    run_verification_batch(db_path, url_client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert get_claim(conn, claim["id"])["status"] == "verified"


def test_command_evidence_without_log_excerpt_is_immediately_suspicious(conn, db_path, agent_and_task):
    agent, task = agent_and_task
    claim = _make_claim(conn, agent, task, "command", {"exit_code": 0})
    conn.commit()

    run_verification_batch(db_path)
    resolved = get_claim(conn, claim["id"])
    assert resolved["status"] == "suspicious"
    assert resolved["resolved_at"] is not None  # terminal, not routed onward


def test_command_evidence_with_log_excerpt_routes_to_llm_review(conn, db_path, agent_and_task):
    agent, task = agent_and_task
    claim = _make_claim(conn, agent, task, "command", {"exit_code": 0, "log_excerpt": "deploy ok"})
    conn.commit()

    run_verification_batch(db_path)
    resolved = get_claim(conn, claim["id"])
    assert resolved["status"] == "needs_llm_review"
    assert resolved["resolved_at"] is None  # not terminal — still in flight


def test_text_evidence_always_routes_to_llm_review(conn, db_path, agent_and_task):
    agent, task = agent_and_task
    claim = _make_claim(conn, agent, task, "text", {})
    conn.commit()

    run_verification_batch(db_path)
    assert get_claim(conn, claim["id"])["status"] == "needs_llm_review"


def test_claim_one_is_only_claimable_once(conn, agent_and_task):
    agent, task = agent_and_task
    claim = _make_claim(conn, agent, task, "text", {})
    conn.commit()
    now = datetime.now(timezone.utc).isoformat()

    assert _claim_one(conn, claim["id"], now) is True
    # a second attempt to claim the same (now 'verifying') claim must fail —
    # this conditional UPDATE is what makes concurrent workers safe.
    assert _claim_one(conn, claim["id"], now) is False


def test_stale_verifying_claim_is_reclaimed_by_the_next_run(conn, db_path, workspace, agent_and_task):
    agent, task = agent_and_task
    (workspace / "out.txt").write_text("hi")
    claim = _make_claim(conn, agent, task, "file", {"path": "out.txt"})
    # simulate a worker that claimed it and then crashed long enough ago to
    # exceed CLAIM_TIMEOUT_SECONDS before ever resolving it.
    stale_time = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    conn.execute(
        "UPDATE claims SET status='verifying', claimed_at=? WHERE id=?",
        (stale_time, claim["id"]),
    )
    conn.commit()

    result = run_verification_batch(db_path)
    assert result["processed"] == 1
    assert get_claim(conn, claim["id"])["status"] == "verified"


def test_fresh_verifying_claim_is_not_touched_by_another_run(conn, db_path, agent_and_task):
    agent, task = agent_and_task
    claim = _make_claim(conn, agent, task, "text", {})
    recent = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE claims SET status='verifying', claimed_at=? WHERE id=?",
        (recent, claim["id"]),
    )
    conn.commit()

    result = run_verification_batch(db_path)
    # it's neither pending nor stale, so this run should see nothing due
    assert result["total"] == 0
    assert get_claim(conn, claim["id"])["status"] == "verifying"


def test_run_verification_batch_processes_multiple_claims_in_one_run(conn, db_path, workspace, agent_and_task):
    agent, task = agent_and_task
    (workspace / "a.txt").write_text("a")
    _make_claim(conn, agent, task, "file", {"path": "a.txt"})
    _make_claim(conn, agent, task, "file", {"path": "missing.txt"})
    _make_claim(conn, agent, task, "text", {})
    conn.commit()

    result = run_verification_batch(db_path)
    assert result["total"] == 3
    assert result["processed"] == 3
