"""The only place that picks the live judge. app/lib/llm_review.py imports
ONLY this function, never a concrete judge class — flipping LLM_JUDGE in
.env from 'mock' to 'anthropic' is the entire swap."""
from app.config import LLM_JUDGE
from app.judges.anthropic_judge import AnthropicJudge
from app.judges.base import LLMJudge
from app.judges.mock_judge import MockJudge


def get_judge(override: LLMJudge | None = None) -> LLMJudge:
    if override is not None:
        return override
    if LLM_JUDGE == "anthropic":
        return AnthropicJudge()
    return MockJudge()
