from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

from app.config import DB_PATH
from app.db import db
from app.deps import require_admin
from app.lib.reports import generate_audit_report, get_report
from app.schemas import GenerateReportRequest

router = APIRouter()


@router.post("/v1/reports", dependencies=[Depends(require_admin)])
def post_report(body: GenerateReportRequest):
    with db(DB_PATH) as conn:
        return generate_audit_report(conn, period_start=body.period_start, period_end=body.period_end)


@router.get("/v1/reports/{report_id}", dependencies=[Depends(require_admin)])
def read_report(report_id: int):
    with db(DB_PATH) as conn:
        return get_report(conn, report_id)


@router.get("/v1/reports/{report_id}/pdf", dependencies=[Depends(require_admin)])
def download_report_pdf(report_id: int):
    with db(DB_PATH) as conn:
        report = get_report(conn, report_id)
    return FileResponse(report["pdf_path"], media_type="application/pdf", filename=f"audit_report_{report_id}.pdf")
