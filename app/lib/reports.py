"""PDF audit reports — the 'reporting' concept. One report = one period's
worth of claims, a status breakdown, and the full list of flagged incidents
with why each one was flagged, so a human gets a readable artifact instead
of having to query the API themselves."""
import json
import os
from datetime import datetime, timezone

from fpdf import FPDF

REPORTS_DIR = os.path.abspath("./reports")

_FLAGGED_STATUSES = ("failed", "suspicious")


def _safe(text: str) -> str:
    """The core Helvetica font only supports Latin-1. Claim text and LLM
    reasoning are arbitrary strings we don't control, so this is a real
    robustness concern, not a hypothetical one — replace anything outside
    that range rather than letting an em-dash or emoji crash report
    generation (caught during build: see BUILDLOG.md)."""
    return text.encode("latin-1", errors="replace").decode("latin-1")


def _claims_in_period(conn, period_start: str, period_end: str) -> list[dict]:
    rows = conn.execute(
        """SELECT c.*, t.title AS task_title
           FROM claims c JOIN tasks t ON t.id = c.task_id
           WHERE c.created_at >= ? AND c.created_at < ?
           ORDER BY c.id""",
        (period_start, period_end),
    ).fetchall()
    return [dict(r) for r in rows]


def _latest_reason(conn, claim_id: int) -> str:
    row = conn.execute(
        "SELECT detail FROM verification_attempts WHERE claim_id = ? ORDER BY id DESC LIMIT 1",
        (claim_id,),
    ).fetchone()
    return row["detail"] if row and row["detail"] else "(no detail recorded)"


def generate_audit_report(conn, *, period_start: str, period_end: str) -> dict:
    os.makedirs(REPORTS_DIR, exist_ok=True)
    claims = _claims_in_period(conn, period_start, period_end)

    status_counts: dict[str, int] = {}
    for c in claims:
        status_counts[c["status"]] = status_counts.get(c["status"], 0) + 1

    flagged = [c for c in claims if c["status"] in _FLAGGED_STATUSES]

    now_iso = datetime.now(timezone.utc).isoformat()

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "ProofOfDone - Audit Report", new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, f"Period: {period_start}  to  {period_end}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 6, f"Generated: {now_iso}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, f"Summary - {len(claims)} claim(s) total", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    for status in ("verified", "failed", "suspicious", "pending", "verifying", "needs_llm_review", "reviewing"):
        count = status_counts.get(status, 0)
        if count:
            pdf.cell(0, 6, f"  {status}: {count}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, f"Flagged incidents ({len(flagged)})", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    if not flagged:
        pdf.cell(0, 6, "None - nothing failed or was marked suspicious this period.", new_x="LMARGIN", new_y="NEXT")
    for c in flagged:
        reason = _safe(_latest_reason(conn, c["id"]))
        pdf.set_font("Helvetica", "B", 9)
        pdf.multi_cell(0, 5, _safe(f"Claim #{c['id']} - task \"{c['task_title']}\" - status: {c['status']}"), new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 9)
        pdf.multi_cell(0, 5, _safe(f"  claim: {c['claim_text']}"), new_x="LMARGIN", new_y="NEXT")
        pdf.multi_cell(0, 5, _safe(f"  evidence_type: {c['evidence_type']}  |  reason: {reason}"), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)

    filename = f"audit_{period_start.replace(':', '-')}_{period_end.replace(':', '-')}.pdf"
    pdf_path = os.path.join(REPORTS_DIR, filename)
    pdf.output(pdf_path)

    summary = {
        "total_claims": len(claims),
        "status_counts": status_counts,
        "flagged_count": len(flagged),
        "flagged_claim_ids": [c["id"] for c in flagged],
    }

    cur = conn.execute(
        """INSERT INTO audit_reports (period_start, period_end, generated_at, summary_json, pdf_path)
           VALUES (?, ?, ?, ?, ?)""",
        (period_start, period_end, now_iso, json.dumps(summary), pdf_path),
    )
    conn.commit()

    return {
        "id": cur.lastrowid,
        "period_start": period_start,
        "period_end": period_end,
        "generated_at": now_iso,
        "summary": summary,
        "pdf_path": pdf_path,
    }


def get_report(conn, report_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM audit_reports WHERE id = ?", (report_id,)).fetchone()
    if row is None:
        return None
    r = dict(row)
    r["summary"] = json.loads(r["summary_json"])
    del r["summary_json"]
    return r
