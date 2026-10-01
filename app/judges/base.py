"""The one interface both judges implement. Nothing outside app/judges/ and
app/judges/registry.py names a concrete judge class — app/lib/llm_review.py
only ever calls registry.get_judge().judge(...), same adapter-indirection
pattern as the social-studio capstone's SocialPublisher registry, for the
same reason: swapping the live judge is a config change, not a code change."""
from typing import Protocol, TypedDict


class JudgeResult(TypedDict):
    verdict: str  # 'plausible' | 'suspicious' | 'insufficient_evidence'
    confidence: float  # 0.0-1.0
    reasoning: str
    input_tokens: int
    output_tokens: int
    cost_micro_cents: int
    raw_response: dict


class LLMJudge(Protocol):
    provider: str
    model: str

    def judge(
        self, *, task_description: str, claim_text: str,
        evidence_type: str, evidence_payload: dict,
    ) -> JudgeResult: ...
