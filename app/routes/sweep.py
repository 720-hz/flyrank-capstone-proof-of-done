from fastapi import APIRouter, Depends

from app.config import DB_PATH
from app.deps import current_agent
from app.lib.sweep import sweep_stale_claims

router = APIRouter()


@router.post("/v1/sweep-runs")
def post_sweep_run(agent: dict = Depends(current_agent)):
    """Meant to be hit by an OS scheduler (cron / Windows Task Scheduler) on a
    fixed interval — see README. Auth is required (any valid agent key) so an
    unauthenticated caller can't trigger it, but it's not agent-scoped: the
    sweep looks at ALL unresolved claims, same as the verification workers."""
    return sweep_stale_claims(DB_PATH)
