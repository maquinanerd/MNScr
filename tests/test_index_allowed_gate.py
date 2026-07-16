from app.editorial_validator import decide_index_allowed


def test_image_stack_warning_does_not_block_indexing():
    result = decide_index_allowed(
        structural_score=60,
        editorial_issues=["IMAGE_STACK_DETECTED"],
        content_type="news",
        word_count=938,
    )

    assert result["allowed"] is True


def test_formatting_warnings_do_not_block_indexing_when_objective_gate_passes():
    result = decide_index_allowed(
        structural_score=60,
        editorial_issues=[
            "CONTENT_TOO_THIN: 300w < 400w para tipo=analysis",
            "CTA_RESIDUAL_DETECTED",
            "RAW_URL_OUTSIDE_CREDIT",
        ],
        content_type="analysis",
        word_count=300,
    )

    assert result["allowed"] is True


def test_word_count_below_absolute_floor_blocks_indexing():
    result = decide_index_allowed(
        structural_score=60,
        editorial_issues=[],
        content_type="news",
        word_count=150,
    )

    assert result["allowed"] is False
    assert "WORD_COUNT_TOO_LOW" in result["reason"]


def test_low_structural_score_blocks_indexing():
    result = decide_index_allowed(
        structural_score=20,
        editorial_issues=[],
        content_type="news",
        word_count=500,
    )

    assert result["allowed"] is False
    assert "SCORE_TOO_LOW" in result["reason"]


def test_qa_llm_not_original_blocks_indexing():
    result = decide_index_allowed(
        structural_score=60,
        editorial_issues=[],
        content_type="news",
        word_count=500,
        qa_llm_result={"has_original_value": False},
    )

    assert result["allowed"] is False
    assert "QA_LLM_NOT_ORIGINAL" in result["reason"]
