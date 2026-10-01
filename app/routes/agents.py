from fastapi import APIRouter

from app.config import DB_PATH
from app.db import db
from app.lib.auth import create_agent
from app.schemas import RegisterAgentRequest

router = APIRouter()


@router.post("/v1/agents")
def register_agent(body: RegisterAgentRequest):
    with db(DB_PATH) as conn:
        agent, plaintext_key = create_agent(conn, name=body.name)
    return {
        "id": agent["id"],
        "name": agent["name"],
        "api_key": plaintext_key,
        "warning": "This key is shown once and never stored in plaintext. Save it now.",
    }
