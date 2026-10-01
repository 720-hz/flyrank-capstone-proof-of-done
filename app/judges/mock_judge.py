"""A deterministic heuristic judge — $0, no key, no network call. Exists so
the whole pipeline (claim -> needs_llm_review -> judged -> resolved) is
provable end to end in this sandbox and in automated tests, exactly like the
mock social publishers in the previous capstone. It is NOT the real
intelligence here; app/judges/anthropic_judge.py is."""
import re

from app.judges.base import JudgeResult

_HEDGE_WORDS = [
    "maybe", "probably", "i think", "should be", "might have", "not sure",
    "i believe", "hopefully", "presumably", "i guess", "possibly",
]
_SPECIFIC_FILE_RE = re.compile(r"[\w\-./\\]+\.\w{2,4}\b")
_SPECIFIC_URL_RE = re.compile(r"https?://\S+")
_DIGIT_RE = re.compile(r"\d+")


class MockJudge:
    provider = "mock"
    model = "heuristic-v1"

    def judge(
        self, *, task_description: str, claim_text: str,
        evidence_type: str, evidence_payload: dict,
    ) -> JudgeResult:
        evidence_text = " ".join(
            str(v) for v in evidence_payload.values() if isinstance(v, (str, int, float))
        )
        full_text = f"{claim_text} {evidence_text}".lower()

        hedge_count = sum(1 for w in _HEDGE_WORDS if w in full_text)
        specific_count = (
            len(_SPECIFIC_FILE_RE.findall(full_text))
            + len(_SPECIFIC_URL_RE.findall(full_text))
            + (1 if _DIGIT_RE.search(full_text) else 0)
        )

        score = 0.5 + 0.15 * specific_count - 0.25 * hedge_count
        confidence = max(0.0, min(1.0, score))

        if hedge_count >= 2 or (hedge_count >= 1 and specific_count == 0):
            verdict = "suspicious"
        elif specific_count == 0 and hedge_count == 0:
            verdict = "insufficient_evidence"
        else:
            verdict = "plausible"

        reasoning = (
            f"heuristic: {hedge_count} hedge word(s), {specific_count} concrete "
            f"detail(s) (file/url/number) found in the claim and evidence text"
        )

        return JudgeResult(
            verdict=verdict,
            confidence=round(confidence, 2),
            reasoning=reasoning,
            input_tokens=0,
            output_tokens=0,
            cost_micro_cents=0,
            raw_response={"mock": True, "hedge_count": hedge_count, "specific_count": specific_count},
        )
