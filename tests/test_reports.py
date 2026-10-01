"""PDF audit reports — the 'reporting' concept. Covers the summary math, the
Latin-1 safety net (_safe), and that a real PDF file lands on disk."""
import os

import pytest

import app.lib.reports as reports
from app.lib.claims import create_claim
from app.lib.reports import _safe, generate_audit_report, get_report


@pytest.fixture(autouse=True)
def reports_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(reports, "REPORTS_DIR", str(tmp_path / "reports"))


@pytest.fixture
def agent_and_task(conn, make_agent, make_task):
    agent, _ = make_agent()
    task = make_task(agent["id"])
    return agent, task


def _resolved_claim(conn, agent, task, status, detail="no file found at 'x'"):
    claim = create_claim(conn, task_id=task["id"], agent_id=agent["id"], claim_text="x",
                          evidence_type="file", evidence_payload={"path": "x"})
    conn.execute("UPDATE claims SET status=? WHERE id=?", (status, claim["id"]))
    conn.execute(
        "INSERT INTO verification_attempts (claim_id, method, outcome, detail, checked_at) "
        "VALUES (?, 'file_hash', 'fail', ?, '2026-01-01T00:00:00+00:00')",
        (claim["id"], detail),
    )
    conn.commit()
    return claim


def test_safe_passes_through_plain_ascii():
    assert _safe("hello world") == "hello world"


def test_safe_replaces_characters_outside_latin1_instead_of_crashing():
    text = "deployed — all good \U0001F680"  # em-dash and an emoji
    result = _safe(text)
    assert "—" not in result
    assert "\U0001F680" not in result
    # doesn't raise, and the ASCII parts survive untouched
    assert "deployed" in result and "all good" in result


def test_generate_audit_report_counts_statuses_correctly(conn, db_path, agent_and_task):
    agent, task = agent_and_task
    _resolved_claim(conn, agent, task, "verified")
    _resolved_claim(conn, agent, task, "failed")
    _resolved_claim(conn, agent, task, "suspicious")
    _resolved_claim(conn, agent, task, "suspicious")

    report = generate_audit_report(conn, period_start="2000-01-01T00:00:00+00:00",
                                    period_end="2100-01-01T00:00:00+00:00")

    assert report["summary"]["total_claims"] == 4
    assert report["summary"]["status_counts"] == {"verified": 1, "failed": 1, "suspicious": 2}
    assert report["summary"]["flagged_count"] == 3  # failed + suspicious, not verified


def test_generate_audit_report_excludes_claims_outside_the_period(conn, db_path, agent_and_task):
    agent, task = agent_and_task
    claim = _resolved_claim(conn, agent, task, "failed")
    conn.execute("UPDATE claims SET created_at='1999-01-01T00:00:00+00:00' WHERE id=?", (claim["id"],))
    conn.commit()

    report = generate_audit_report(conn, period_start="2020-01-01T00:00:00+00:00",
                                    period_end="2030-01-01T00:00:00+00:00")

    assert report["summary"]["total_claims"] == 0


def test_generate_audit_report_writes_a_real_pdf_to_disk(conn, db_path, agent_and_task):
    agent, task = agent_and_task
    _resolved_claim(conn, agent, task, "failed")

    report = generate_audit_report(conn, period_start="2000-01-01T00:00:00+00:00",
                                    period_end="2100-01-01T00:00:00+00:00")

    assert os.path.isfile(report["pdf_path"])
    with open(report["pdf_path"], "rb") as f:
        assert f.read(5) == b"%PDF-"


def test_generate_audit_report_never_crashes_on_non_latin1_claim_text(conn, db_path, agent_and_task):
    agent, task = agent_and_task
    claim = create_claim(conn, task_id=task["id"], agent_id=agent["id"],
                          claim_text="shipped it \U0001F680 — done", evidence_type="file",
                          evidence_payload={"path": "x"})
    conn.execute("UPDATE claims SET status='failed' WHERE id=?", (claim["id"],))
    conn.commit()

    report = generate_audit_report(conn, period_start="2000-01-01T00:00:00+00:00",
                                    period_end="2100-01-01T00:00:00+00:00")
    assert os.path.isfile(report["pdf_path"])  # didn't raise FPDFUnicodeEncodingException


def test_get_report_round_trips_summary_json(conn, db_path, agent_and_task):
    agent, task = agent_and_task
    _resolved_claim(conn, agent, task, "verified")
    created = generate_audit_report(conn, period_start="2000-01-01T00:00:00+00:00",
                                      period_end="2100-01-01T00:00:00+00:00")

    fetched = get_report(conn, created["id"])
    assert fetched["summary"] == created["summary"]
    assert "summary_json" not in fetched


def test_get_report_returns_none_for_missing_id(conn, db_path):
    assert get_report(conn, 999999) is None
