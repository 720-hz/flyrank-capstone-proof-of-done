"""The second durable worker: claims already routed to 'needs_llm_review' get
judged and resolved. Uses MockJudge (and small throwaway judges) so this whole
pipeline is provable with $0 and no network access."""
from datetime import datetime, timedelta, timezone

import pytest

from app.judges.base import JudgeResult
from app.judges.mock_judge import MockJudge
from app.lib.claims import create_claim, get_claim
from app.lib.llm_review import _claim_one, run_llm_review_batch


@pytest.fixture
def agent_and_task(conn, make_agent, make_task):
    agent, _ = make_agent()
    task = make_task(agent["id"])
    return agent, task


class _FixedJudge:
    """A throwaway third judge implementation, same role as the injectable
    third publisher in the social-studio capstone's adapter test: proves the
    worker only ever talks to the LLMJudge interface, never a concrete class."""

    provider = "fixed"
    model = "always-the-same"

    def __init__(self, result: JudgeResult):
        self._result = result

    def judge(self, **kwargs) -> JudgeResult:
        return self._result


def _needs_review_claim(conn, agent, task, evidence_type="text", payload=None, text="did it"):
    claim = create_claim(
        conn, task_id=task["id"], agent_id=agent["id"], claim_text=text,
        evidence_type=evidence_type, evidence_payload=payload or {},
    )
    # text evidence always lands in needs_llm_review via the verification
    # worker; set it directly here so this file can test llm_review.py in
    # isolation without depending on verification.py's routing.
    conn.execute("UPDATE claims SET status='needs_llm_review' WHERE id=?", (claim["id"],))
    conn.commit()
    return claim


def _result(verdict, confidence, reasoning="because"):
    return JudgeResult(
        verdict=verdict, confidence=confidence, reasoning=reasoning,
        input_tokens=10, output_tokens=5, cost_micro_cents=0, raw_response={},
    )


def test_confident_plausible_verdict_resolves_to_verified(conn, db_path, agent_and_task):
    agent, task = agent_and_task
    claim = _needs_review_claim(conn, agent, task)
    judge = _FixedJudge(_result("plausible", 0.9))

    run_llm_review_batch(db_path, judge=judge)
    assert get_claim(conn, claim["id"])["status"] == "verified"


def test_low_confidence_plausible_is_conservatively_flagged(conn, db_path, agent_and_task):
    agent, task = agent_and_task
    claim = _needs_review_claim(conn, agent, task)
    # 'plausible' but below the 0.6 trust threshold -> must not be silently accepted
    judge = _FixedJudge(_result("plausible", 0.4))

    run_llm_review_batch(db_path, judge=judge)
    assert get_claim(conn, claim["id"])["status"] == "suspicious"


def test_suspicious_verdict_resolves_to_suspicious(conn, db_path, agent_and_task):
    agent, task = agent_and_task
    claim = _needs_review_claim(conn, agent, task)
    judge = _FixedJudge(_result("suspicious", 0.1))

    run_llm_review_batch(db_path, judge=judge)
    assert get_claim(conn, claim["id"])["status"] == "suspicious"


def test_insufficient_evidence_resolves_to_suspicious(conn, db_path, agent_and_task):
    agent, task = agent_and_task
    claim = _needs_review_claim(conn, agent, task)
    judge = _FixedJudge(_result("insufficient_evidence", 0.5))

    run_llm_review_batch(db_path, judge=judge)
    assert get_claim(conn, claim["id"])["status"] == "suspicious"


def test_review_writes_an_llm_reviews_row_with_cost_and_tokens(conn, db_path, agent_and_task):
    agent, task = agent_and_task
    claim = _needs_review_claim(conn, agent, task)
    judge = _FixedJudge(_result("plausible", 0.95, reasoning="specific and consistent"))

    run_llm_review_batch(db_path, judge=judge)

    row = conn.execute("SELECT * FROM llm_reviews WHERE claim_id=?", (claim["id"],)).fetchone()
    assert row["provider"] == "fixed"
    assert row["verdict"] == "plausible"
    assert row["reasoning"] == "specific and consistent"
    assert row["input_tokens"] == 10
    assert row["output_tokens"] == 5


def test_review_also_appends_a_verification_attempt_so_reports_see_it(conn, db_path, agent_and_task):
    agent, task = agent_and_task
    claim = _needs_review_claim(conn, agent, task)
    judge = _FixedJudge(_result("suspicious", 0.2, reasoning="vague hand-wave"))

    run_llm_review_batch(db_path, judge=judge)

    latest = conn.execute(
        "SELECT * FROM verification_attempts WHERE claim_id=? ORDER BY id DESC LIMIT 1", (claim["id"],)
    ).fetchone()
    assert latest["method"] == "llm_review"
    assert "vague hand-wave" in latest["detail"]


def test_default_judge_is_mock_without_an_override(conn, db_path, agent_and_task, monkeypatch):
    # with LLM_JUDGE left at its 'mock' default, no override should still work
    # end to end — this is the $0, no-key, no-network path.
    monkeypatch.setattr("app.judges.registry.LLM_JUDGE", "mock")
    agent, task = agent_and_task
    claim = _needs_review_claim(conn, agent, task, text="I think I maybe finished it, probably")

    run_llm_review_batch(db_path)  # no judge= override at all
    row = conn.execute("SELECT provider, model FROM llm_reviews WHERE claim_id=?", (claim["id"],)).fetchone()
    assert row["provider"] == "mock"
    assert get_claim(conn, claim["id"])["status"] == "suspicious"  # hedge-word-heavy claim


def test_claim_one_is_only_claimable_once(conn, agent_and_task):
    agent, task = agent_and_task
    claim = _needs_review_claim(conn, agent, task)
    now = datetime.now(timezone.utc).isoformat()

    assert _claim_one(conn, claim["id"], now) is True
    assert _claim_one(conn, claim["id"], now) is False


def test_stale_reviewing_claim_is_reclaimed(conn, db_path, agent_and_task):
    agent, task = agent_and_task
    claim = _needs_review_claim(conn, agent, task)
    stale_time = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    conn.execute(
        "UPDATE claims SET status='reviewing', claimed_at=? WHERE id=?",
        (stale_time, claim["id"]),
    )
    conn.commit()

    result = run_llm_review_batch(db_path, judge=_FixedJudge(_result("plausible", 0.99)))
    assert result["processed"] == 1
    assert get_claim(conn, claim["id"])["status"] == "verified"


def test_mock_judge_heuristic_rewards_specific_evidence():
    judge = MockJudge()
    confident = judge.judge(
        task_description="write README.md",
        claim_text="I wrote README.md with 3 sections and pushed commit a1b2c3d",
        evidence_type="text", evidence_payload={},
    )
    hedgy = judge.judge(
        task_description="write README.md",
        claim_text="I think I probably wrote something, not totally sure",
        evidence_type="text", evidence_payload={},
    )
    assert confident["verdict"] == "plausible"
    assert hedgy["verdict"] == "suspicious"
    assert confident["confidence"] > hedgy["confidence"]


def test_mock_judge_is_free_and_deterministic():
    judge = MockJudge()
    kwargs = dict(task_description="x", claim_text="wrote out.txt", evidence_type="file",
                  evidence_payload={"path": "out.txt"})
    first = judge.judge(**kwargs)
    second = judge.judge(**kwargs)
    assert first == second
    assert first["cost_micro_cents"] == 0
