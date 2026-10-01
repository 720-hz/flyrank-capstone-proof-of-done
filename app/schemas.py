from pydantic import BaseModel, Field


class RegisterAgentRequest(BaseModel):
    name: str


class CreateTaskRequest(BaseModel):
    title: str
    description: str


class CreateClaimRequest(BaseModel):
    task_id: int
    claim_text: str
    evidence_type: str = Field(pattern="^(file|url|text|command)$")
    evidence_payload: dict = {}


class GenerateReportRequest(BaseModel):
    period_start: str  # ISO 8601
    period_end: str
