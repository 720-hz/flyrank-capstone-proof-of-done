from fastapi import APIRouter, Depends, HTTPException

from app.config import DB_PATH
from app.db import db
from app.deps import current_agent
from app.lib.auth import require_owns_claim, require_owns_task
from app.lib.claims import create_claim, get_claim, list_attempts, list_claims
from app.lib.errors import ForbiddenError, ValidationError
from app.lib.llm_review import run_llm_review_batch
from app.lib.verification import run_verification_batch
from app.schemas import CreateClaimRequest

router = APIRouter()


@router.post("/v1/claims")
def post_claim(body: CreateClaimRequest, agent: dict = Depends(current_agent)):
    with db(DB_PATH) as conn:
        try:
            require_owns_task(conn, agent, body.task_id)
        except ForbiddenError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
        try:
            return create_claim(
                conn,
                task_id=body.task_id,
                agent_id=agent["id"],
                claim_text=body.claim_text,
                evidence_type=body.evidence_type,
                evidence_payload=body.evidence_payload,
            )
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc))


@router.get("/v1/claims")
def get_claims(status: str | None = None, agent: dict = Depends(current_agent)):
    with db(DB_PATH) as conn:
        return {"claims": list_claims(conn, agent_id=agent["id"], status=status)}


@router.get("/v1/claims/{claim_id}")
def read_claim(claim_id: int, agent: dict = Depends(current_agent)):
    with db(DB_PATH) as conn:
        try:
            require_owns_claim(conn, agent, claim_id)
        except ForbiddenError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
        return get_claim(conn, claim_id)


@router.get("/v1/claims/{claim_id}/attempts")
def read_attempts(claim_id: int, agent: dict = Depends(current_agent)):
    with db(DB_PATH) as conn:
        try:
            require_owns_claim(conn, agent, claim_id)
        except ForbiddenError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
        return {"attempts": list_attempts(conn, claim_id)}


@router.post("/v1/verification-runs")
def post_verification_run(agent: dict = Depends(current_agent)):
    # any authenticated agent can trigger a sweep of ALL due claims (not just its
    # own) — this is the worker endpoint, not a per-agent data endpoint; ownership
    # is still enforced everywhere claim/task DATA is read or written.
    return run_verification_batch(DB_PATH)


@router.post("/v1/llm-review-runs")
def post_llm_review_run(agent: dict = Depends(current_agent)):
    return run_llm_review_batch(DB_PATH)
