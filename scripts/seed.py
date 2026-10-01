"""Seeds a demo agent, a task, and one claim per (evidence_type, outcome)
combination the server can produce, then actually runs the verification
worker / LLM review worker / SLA sweep against them so every status in
CLAIM_STATUSES is represented by a real resolved row, not a hand-faked one.

Run with: python3 scripts/seed.py
Safe to re-run — it always starts from a fresh ./proof_of_done.db (per
DB_PATH in .env) unless you point DB_PATH elsewhere first.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import ADMIN_API_KEY, DB_PATH, WORKSPACE_ROOT  # noqa: E402
from app.db import db, init_db  # noqa: E402
from app.lib.auth import create_agent  # noqa: E402
from app.lib.claims import create_claim  # noqa: E402
from app.lib.llm_review import run_llm_review_batch  # noqa: E402
from app.lib.sweep import sweep_stale_claims  # noqa: E402
from app.lib.tasks import create_task  # noqa: E402
from app.lib.verification import run_verification_batch  # noqa: E402


def main():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    init_db(DB_PATH)
    os.makedirs(WORKSPACE_ROOT, exist_ok=True)

    with open(os.path.join(WORKSPACE_ROOT, "q3-report.csv"), "w") as f:
        f.write("quarter,revenue\nQ3,41200\n")

    with db(DB_PATH) as conn:
        agent, plaintext_key = create_agent(conn, name="demo-agent")
        task = create_task(
            conn, agent_id=agent["id"], title="Publish the Q3 report",
            description="Write q3-report.csv to the workspace and post a summary in #finance.",
        )

        # 1. file evidence, real file -> deterministic 'verified'
        create_claim(
            conn, task_id=task["id"], agent_id=agent["id"],
            claim_text="Wrote q3-report.csv to the workspace root.",
            evidence_type="file", evidence_payload={"path": "q3-report.csv"},
        )

        # 2. file evidence, no such file -> deterministic 'failed'
        create_claim(
            conn, task_id=task["id"], agent_id=agent["id"],
            claim_text="Wrote q4-report.csv to the workspace root.",
            evidence_type="file", evidence_payload={"path": "q4-report.csv"},
        )

        # 3. url evidence, real HTTP check against a host that should always
        #    be up -> deterministic 'verified' (needs real internet access;
        #    check_url_evidence fails gracefully rather than crashing if not)
        create_claim(
            conn, task_id=task["id"], agent_id=agent["id"],
            claim_text="Confirmed the status page is reachable.",
            evidence_type="url",
            evidence_payload={"url": "https://example.com", "expect_status": 200, "expect_contains": "Example"},
        )

        # 4. text evidence, specific and detailed -> routed to LLM review,
        #    MockJudge should find it plausible
        create_claim(
            conn, task_id=task["id"], agent_id=agent["id"],
            claim_text="Posted the Q3 summary (revenue $41,200, up 6%) to #finance at 2pm.",
            evidence_type="text", evidence_payload={},
        )

        # 5. text evidence, hedgy and vague -> routed to LLM review,
        #    MockJudge should flag it suspicious
        create_claim(
            conn, task_id=task["id"], agent_id=agent["id"],
            claim_text="I think I probably posted something about the report, not totally sure.",
            evidence_type="text", evidence_payload={},
        )

        # 6. command evidence with no log_excerpt -> immediately 'suspicious'
        #    (a self-reported exit code alone is never trusted)
        create_claim(
            conn, task_id=task["id"], agent_id=agent["id"],
            claim_text="Ran the publish script, exit code 0.",
            evidence_type="command", evidence_payload={"claimed_exit_code": 0},
        )

        # 7. command evidence with a log excerpt -> routed to LLM review
        create_claim(
            conn, task_id=task["id"], agent_id=agent["id"],
            claim_text="Ran the publish script, exit code 0.",
            evidence_type="command",
            evidence_payload={"claimed_exit_code": 0, "log_excerpt": "[publish] uploaded q3-report.csv (12KB) ... done"},
        )

    verify_result = run_verification_batch(DB_PATH)
    review_result = run_llm_review_batch(DB_PATH)  # MockJudge by default (LLM_JUDGE=mock)

    # 8. a claim created AFTER both worker runs above and immediately
    #    backdated, so neither worker ever saw it — only the SLA sweep
    #    catches it, demonstrating it's a genuinely separate mechanism
    #    (a scheduled sweep, not a third queue-draining worker).
    with db(DB_PATH) as conn:
        forgotten = create_claim(
            conn, task_id=task["id"], agent_id=agent["id"],
            claim_text="Will follow up with the final numbers later.",
            evidence_type="text", evidence_payload={},
        )
        long_ago = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        conn.execute("UPDATE claims SET created_at=? WHERE id=?", (long_ago, forgotten["id"]))

    sweep_result = sweep_stale_claims(DB_PATH)

    print("=" * 72)
    print("Seed complete.")
    print("=" * 72)
    print(f"Demo agent API key (shown once, exactly like a real registration): {plaintext_key}")
    print(f"Admin key (from .env, ADMIN_API_KEY): {ADMIN_API_KEY!r}")
    print()
    print(f"verification-run:  {verify_result}")
    print(f"llm-review-run:    {review_result}")
    print(f"sweep-run:         {sweep_result}")
    print()
    print("Try it:")
    print(f'  curl -s http://localhost:8000/v1/claims -H "Authorization: Bearer {plaintext_key}" | python3 -m json.tool')
    print(
        '  curl -s -X POST http://localhost:8000/v1/reports '
        f'-H "Authorization: Bearer {ADMIN_API_KEY}" -H "Content-Type: application/json" '
        '-d \'{"period_start":"2000-01-01T00:00:00+00:00","period_end":"2100-01-01T00:00:00+00:00"}\''
    )
    print("See README.md for the full walkthrough.")


if __name__ == "__main__":
    main()
