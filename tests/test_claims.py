"""claims.py — creation, validation, and reads. The 'Database' + 'API
endpoints' concepts meet here: this is the CRUD layer the routes call."""
import pytest

from app.lib.claims import create_claim, get_claim, list_attempts, list_claims
from app.lib.errors import ValidationError


def test_create_claim_defaults_to_pending(conn, make_agent, make_task):
    agent, _ = make_agent()
    task = make_task(agent["id"])
    claim = create_claim(
        conn, task_id=task["id"], agent_id=agent["id"], claim_text="wrote the file",
        evidence_type="file", evidence_payload={"path": "out.txt"},
    )
    assert claim["status"] == "pending"
    assert claim["evidence_payload"] == {"path": "out.txt"}
    assert claim["resolved_at"] is None


def test_create_claim_rejects_unknown_evidence_type(conn, make_agent, make_task):
    agent, _ = make_agent()
    task = make_task(agent["id"])
    with pytest.raises(ValidationError):
        create_claim(
            conn, task_id=task["id"], agent_id=agent["id"], claim_text="x",
            evidence_type="telepathy", evidence_payload={},
        )


def test_get_claim_round_trips_payload_as_dict(conn, make_agent, make_task):
    agent, _ = make_agent()
    task = make_task(agent["id"])
    created = create_claim(
        conn, task_id=task["id"], agent_id=agent["id"], claim_text="x",
        evidence_type="url", evidence_payload={"url": "https://example.com", "expect_status": 200},
    )
    fetched = get_claim(conn, created["id"])
    assert fetched["evidence_payload"] == {"url": "https://example.com", "expect_status": 200}


def test_get_claim_returns_none_for_missing_id(conn):
    assert get_claim(conn, 999999) is None


def test_list_claims_filters_by_agent_and_status(conn, make_agent, make_task):
    a1, _ = make_agent("a1")
    a2, _ = make_agent("a2")
    t1 = make_task(a1["id"])
    t2 = make_task(a2["id"])
    c1 = create_claim(conn, task_id=t1["id"], agent_id=a1["id"], claim_text="x",
                       evidence_type="text", evidence_payload={})
    create_claim(conn, task_id=t2["id"], agent_id=a2["id"], claim_text="y",
                 evidence_type="text", evidence_payload={})

    only_a1 = list_claims(conn, agent_id=a1["id"])
    assert [c["id"] for c in only_a1] == [c1["id"]]

    conn.execute("UPDATE claims SET status='verified' WHERE id=?", (c1["id"],))
    assert list_claims(conn, agent_id=a1["id"], status="verified")[0]["id"] == c1["id"]
    assert list_claims(conn, agent_id=a1["id"], status="pending") == []


def test_list_attempts_is_ordered_and_scoped_to_the_claim(conn, make_agent, make_task):
    agent, _ = make_agent()
    task = make_task(agent["id"])
    c1 = create_claim(conn, task_id=task["id"], agent_id=agent["id"], claim_text="x",
                       evidence_type="text", evidence_payload={})
    c2 = create_claim(conn, task_id=task["id"], agent_id=agent["id"], claim_text="y",
                       evidence_type="text", evidence_payload={})
    conn.execute(
        "INSERT INTO verification_attempts (claim_id, method, outcome, detail, checked_at) "
        "VALUES (?, 'llm_review', 'inconclusive', 'first', '2026-01-01T00:00:00+00:00')",
        (c1["id"],),
    )
    conn.execute(
        "INSERT INTO verification_attempts (claim_id, method, outcome, detail, checked_at) "
        "VALUES (?, 'llm_review', 'pass', 'second', '2026-01-01T00:00:01+00:00')",
        (c1["id"],),
    )
    conn.execute(
        "INSERT INTO verification_attempts (claim_id, method, outcome, detail, checked_at) "
        "VALUES (?, 'llm_review', 'fail', 'not mine', '2026-01-01T00:00:02+00:00')",
        (c2["id"],),
    )
    conn.commit()

    attempts = list_attempts(conn, c1["id"])
    assert [a["detail"] for a in attempts] == ["first", "second"]
