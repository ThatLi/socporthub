from fastapi import APIRouter, Depends, HTTPException

from api.auth import get_current_admin
from api.config import get_settings
from api.schemas import AiReviewRequest
from api.services.ai_reviewer import OpenAIReviewer, ReviewResult, load_rubrics

router = APIRouter(prefix="/api/ai-review", tags=["ai-review"])


@router.post("", response_model=ReviewResult)
async def review_document(req: AiReviewRequest, _admin=Depends(get_current_admin)) -> ReviewResult:
    settings = get_settings()
    if not settings.ai_reviewer_enabled:
        raise HTTPException(404, "AI reviewer is not enabled")
    if len(req.document_text) > settings.ai_reviewer_max_chars:
        raise HTTPException(413, "The document is too long to review")
    try:
        reviewer = OpenAIReviewer(
            settings.openai_api_key,
            model=settings.ai_reviewer_model,
            timeout=settings.ai_reviewer_timeout_seconds,
        )
        return await reviewer.review(req, load_rubrics())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc
