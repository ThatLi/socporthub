from api.services.ai_reviewer import LocalStubReviewer, ReviewRequest, build_review_prompt, load_rubrics


def test_rubric_file_loads_and_local_stub_returns_valid_result():
    rubrics = load_rubrics()
    request = ReviewRequest(
        proposal_title="Movie Night",
        proposal_category="event",
        document_text="Movie Night\nBudget: $100",
    )
    result = LocalStubReviewer().review(request, rubrics)
    assert result.suggestions[0].rule_id == "physical-movement"
    assert result.suggestions[0].status == "pending"
    assert "Movie Night" in build_review_prompt(request, rubrics)
