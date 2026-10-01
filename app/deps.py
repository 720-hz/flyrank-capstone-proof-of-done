"""FastAPI dependencies: turn the auth lib's exceptions into real HTTP status
codes, in one place, so every route gets the same enforcement for free."""
from fastapi import Header, HTTPException

from app.config import DB_PATH
from app.db import get_connection
from app.lib.auth import authenticate_admin, authenticate_agent
from app.lib.errors import AuthError


def current_agent(authorization: str | None = Header(default=None)) -> dict:
    conn = get_connection(DB_PATH)
    try:
        return authenticate_agent(conn, authorization)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    finally:
        conn.close()


def require_admin(authorization: str | None = Header(default=None)) -> None:
    try:
        authenticate_admin(authorization)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
