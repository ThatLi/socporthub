#!/usr/bin/env python3
"""Run the local rubric reviewer.

Example:
  python scripts/run_ai_review.py --category event --title "Movie Night" \
    --document /tmp/movie-night.txt
"""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.config import get_settings
from api.services.ai_reviewer import GeminiReviewer, LocalStubReviewer, ReviewRequest, load_rubrics


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local AI reviewer skeleton")
    parser.add_argument("--category", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--document", required=True, type=Path)
    parser.add_argument("--rubrics", default="config/ai_reviewer_rubrics.json", type=Path)
    parser.add_argument("--provider", choices=("local", "gemini"), default="local")
    args = parser.parse_args()
    request = ReviewRequest(
        proposal_title=args.title,
        proposal_category=args.category,
        document_text=args.document.read_text(encoding="utf-8"),
    )
    rubrics = load_rubrics(args.rubrics)
    if args.provider == "gemini":
        import asyncio
        settings = get_settings()
        result = asyncio.run(GeminiReviewer(
            settings.gemini_api_key,
            model=settings.ai_reviewer_model,
            timeout=settings.ai_reviewer_timeout_seconds,
        ).review(request, rubrics))
    else:
        result = LocalStubReviewer().review(request, rubrics)
    print(json.dumps(result.model_dump(), indent=2))


if __name__ == "__main__":
    main()
