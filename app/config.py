"""All tunable constants in one place."""
import os
from dotenv import load_dotenv

load_dotenv()

DB_PATH = os.getenv("DB_PATH", "./proof_of_done.db")
PORT = int(os.getenv("PORT", "8000"))

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5")
ANTHROPIC_API_BASE = "https://api.anthropic.com"
LLM_JUDGE = os.getenv("LLM_JUDGE", "mock")  # 'mock' or 'anthropic'

WORKSPACE_ROOT = os.path.abspath(os.getenv("WORKSPACE_ROOT", "./workspace"))

SLA_SECONDS = int(os.getenv("SLA_SECONDS", "300"))
CLAIM_TIMEOUT_SECONDS = int(os.getenv("CLAIM_TIMEOUT_SECONDS", "60"))

ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "")

EVIDENCE_TYPES = {"file", "url", "text", "command"}
CLAIM_STATUSES = {
    "pending", "verifying", "verified", "failed", "suspicious",
    "needs_llm_review", "reviewing",
}
