from fastapi import APIRouter, Depends, HTTPException

from app.config import DB_PATH
from app.db import db
from app.deps import current_agent
from app.lib.auth import require_owns_task
from app.lib.errors import ForbiddenError
from app.lib.tasks import create_task, get_task, list_tasks
from app.schemas import CreateTaskRequest

router = APIRouter()


@router.post("/v1/tasks")
def post_task(body: CreateTaskRequest, agent: dict = Depends(current_agent)):
    with db(DB_PATH) as conn:
        return create_task(conn, agent_id=agent["id"], title=body.title, description=body.description)


@router.get("/v1/tasks")
def get_tasks(agent: dict = Depends(current_agent)):
    with db(DB_PATH) as conn:
        return {"tasks": list_tasks(conn, agent_id=agent["id"])}


@router.get("/v1/tasks/{task_id}")
def read_task(task_id: int, agent: dict = Depends(current_agent)):
    with db(DB_PATH) as conn:
        try:
            return require_owns_task(conn, agent, task_id)
        except ForbiddenError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
