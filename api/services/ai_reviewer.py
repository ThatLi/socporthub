"""Rubric-driven AI reviewer.

The local stub is retained for offline tests. OpenAIReviewer is an HTTP adapter
for the Responses API and returns the same validated result contract.
"""

import json
from pathlib import Path
from typing import Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field


class ReviewRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1)
    category: str = Field(min_length=1)
    severity: str = Field(pattern="^(low|medium|high)$")
    instruction: str = Field(min_length=1)


class ReviewerRubrics(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)
    review_objective: str
    principles: list[str] = Field(default_factory=list)
    output_sections: list[str] = Field(default_factory=list)
    global_rules: list[ReviewRule] = Field(default_factory=list)


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    proposal_title: str
    proposal_category: str
    proposal_description: str = ""
    document_text: str = Field(min_length=1)


class ReviewSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rule_id: str
    category: str
    severity: str = Field(pattern="^(low|medium|high)$")
    page: str = ""
    evidence: str = ""
    concern: str
    existing_mitigation: str = ""
    gap: str = ""
    recommended_clarification: str
    confidence: float = Field(ge=0, le=1)
    status: str = Field(default="pending", pattern="^(pending|approved|rejected|posted)$")


class ReviewResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str
    suggestions: list[ReviewSuggestion] = Field(default_factory=list)


class Reviewer(Protocol):
    def review(self, request: ReviewRequest, rubrics: ReviewerRubrics) -> ReviewResult:
        ...


def load_rubrics(path: str | Path = "config/ai_reviewer_rubrics.json") -> ReviewerRubrics:
    rubric_path = Path(path)
    try:
        payload = json.loads(rubric_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Rubric file not found: {rubric_path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Rubric file is not valid JSON: {exc}") from exc
    try:
        return ReviewerRubrics.model_validate(payload)
    except Exception as exc:
        raise ValueError(f"Rubric file failed validation: {exc}") from exc


def build_review_prompt(request: ReviewRequest, rubrics: ReviewerRubrics) -> str:
    rules = "\n".join(
        f"- [{rule.id}] ({rule.severity}) {rule.instruction}"
        for rule in rubrics.global_rules
    )
    return f"""You review a student proposal. Use only the proposal and rules below.
Flag potential concerns for human review; do not make the institutional decision.
Review the entire document. Do not invent facts, rules, page numbers, or quotations.
Every substantive finding must include an exact quotation, page number when available,
existing mitigation, remaining gap, and a recommended clarification or action.
Return JSON matching the ReviewResult schema.

Objective: {rubrics.review_objective}
Principles:\n{chr(10).join(f'- {principle}' for principle in rubrics.principles)}
Proposal title: {request.proposal_title}
Proposal category: {request.proposal_category}
Proposal description: {request.proposal_description}

Review rules:
{rules or '- No rules configured yet.'}

Document text:
{request.document_text}
"""


class LocalStubReviewer:
    """Local provider that emits one transparent draft per configured rule."""

    def review(self, request: ReviewRequest, rubrics: ReviewerRubrics) -> ReviewResult:
        suggestions = [
            ReviewSuggestion(
                rule_id=rule.id,
                category=rule.category,
                severity=rule.severity,
                concern=rule.instruction,
                recommended_clarification=f"Please review this point against the rubric: {rule.instruction}",
                confidence=0,
            )
            for rule in rubrics.global_rules
        ]
        return ReviewResult(
            summary=f"Local stub generated {len(suggestions)} rubric checks using the common proposal rubric.",
            suggestions=suggestions,
        )


def _structured_output_schema() -> dict:
    suggestion = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "rule_id": {"type": "string"},
            "category": {"type": "string"},
            "severity": {"type": "string", "enum": ["low", "medium", "high"]},
            "page": {"type": "string"},
            "evidence": {"type": "string"},
            "concern": {"type": "string"},
            "existing_mitigation": {"type": "string"},
            "gap": {"type": "string"},
            "recommended_clarification": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "status": {"type": "string", "enum": ["pending"]}
        },
        "required": ["rule_id", "category", "severity", "page", "evidence", "concern", "existing_mitigation", "gap", "recommended_clarification", "confidence", "status"]
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "summary": {"type": "string"},
            "suggestions": {"type": "array", "items": suggestion}
        },
        "required": ["summary", "suggestions"]
    }


class OpenAIReviewer:
    def __init__(self, api_key: str, model: str = "gpt-5-mini", timeout: float = 90) -> None:
        if not api_key:
            raise ValueError("OPENAI_API_KEY is not configured")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    async def review(self, request: ReviewRequest, rubrics: ReviewerRubrics) -> ReviewResult:
        payload = {
            "model": self.model,
            "store": False,
            "input": [
                {"role": "system", "content": [{"type": "input_text", "text": "You are a careful proposal safety reviewer."}]},
                {"role": "user", "content": [{"type": "input_text", "text": build_review_prompt(request, rubrics)}]}
            ],
            "text": {"format": {"type": "json_schema", "name": "proposal_review", "strict": True, "schema": _structured_output_schema()}}
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    "https://api.openai.com/v1/responses",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=payload,
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(f"OpenAI review request failed ({exc.response.status_code}): {exc.response.text[:500]}") from exc
        except httpx.HTTPError as exc:
            raise RuntimeError(f"OpenAI review request could not be completed: {exc}") from exc

        data = response.json()
        if data.get("status") not in (None, "completed"):
            raise RuntimeError(f"OpenAI review did not complete: {data.get('status', 'unknown status')}")
        output_text = data.get("output_text") or "".join(
            item.get("text", "")
            for message in data.get("output", [])
            for item in message.get("content", [])
            if item.get("type") == "output_text"
        )
        if not output_text:
            raise RuntimeError("OpenAI review returned no structured output")
        try:
            return ReviewResult.model_validate(json.loads(output_text))
        except (json.JSONDecodeError, ValueError) as exc:
            raise RuntimeError("OpenAI review returned invalid structured output") from exc
