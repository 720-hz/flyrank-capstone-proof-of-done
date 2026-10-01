import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import WORKSPACE_ROOT
from app.db import init_db
from app.routes import agents, claims, reports, sweep, tasks


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    os.makedirs(WORKSPACE_ROOT, exist_ok=True)
    yield


app = FastAPI(title="ProofOfDone", lifespan=lifespan)

app.include_router(agents.router)
app.include_router(tasks.router)
app.include_router(claims.router)
app.include_router(sweep.router)
app.include_router(reports.router)


@app.get("/health")
def health():
    return {"status": "ok"}
