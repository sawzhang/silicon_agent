"""Tests for Phase 1.3: External Evaluator — composite scoring."""
from __future__ import annotations

from app.worker.evaluator import (
    EvaluationResult,
    compute_composite_score,
    extract_signals_from_stage_outputs,
)


def test_compute_composite_score_all_pass():
    score = compute_composite_score(
        llm_confidence=0.9,
        external_signals={
            "test_pass_rate": 1.0,
            "build_success": True,
            "lint_clean": True,
        },
    )
    assert score > 0.85


def test_compute_composite_score_build_failed():
    score = compute_composite_score(
        llm_confidence=0.9,
        external_signals={
            "test_pass_rate": 1.0,
            "build_success": False,
            "lint_clean": True,
        },
    )
    # build_success=False with weight 0.2 should pull score down significantly
    assert score < 0.9


def test_compute_composite_score_test_partial():
    score = compute_composite_score(
        llm_confidence=0.8,
        external_signals={
            "test_pass_rate": 0.5,
            "build_success": True,
            "lint_clean": True,
        },
    )
    # 0.3*0.8 + 0.4*0.5 + 0.2*1.0 + 0.1*1.0 = 0.24 + 0.20 + 0.20 + 0.10 = 0.74
    assert 0.70 <= score <= 0.78


def test_compute_composite_score_no_external_signals():
    score = compute_composite_score(
        llm_confidence=0.75,
        external_signals={},
    )
    # Should fall back to pure LLM confidence
    assert score == 0.75


def test_compute_composite_score_custom_weights():
    custom_weights = {
        "llm_confidence": 0.5,
        "test_pass_rate": 0.5,
    }
    score = compute_composite_score(
        llm_confidence=1.0,
        external_signals={"test_pass_rate": 0.0},
        weights=custom_weights,
    )
    # 0.5*1.0 + 0.5*0.0 = 0.5
    assert abs(score - 0.5) < 0.01


def test_compute_composite_score_clamps_to_range():
    score = compute_composite_score(
        llm_confidence=1.5,  # over 1.0
        external_signals={},
    )
    assert score == 1.0

    score = compute_composite_score(
        llm_confidence=-0.5,  # under 0.0
        external_signals={},
    )
    assert score == 0.0


def test_extract_signals_from_verify_output():
    structured_outputs = {
        "verify": {
            "build_success": True,
            "lint_clean": False,
        },
    }
    signals = extract_signals_from_stage_outputs(structured_outputs)
    assert signals["build_success"] is True
    assert signals["lint_clean"] is False


def test_extract_signals_from_test_output():
    structured_outputs = {
        "test": {
            "tests_passed": 8,
            "tests_total": 10,
        },
    }
    signals = extract_signals_from_stage_outputs(structured_outputs)
    assert abs(signals["test_pass_rate"] - 0.8) < 0.01


def test_extract_signals_from_test_output_with_rate():
    structured_outputs = {
        "test": {
            "test_pass_rate": 0.95,
        },
    }
    signals = extract_signals_from_stage_outputs(structured_outputs)
    assert signals["test_pass_rate"] == 0.95


def test_extract_signals_empty_outputs():
    signals = extract_signals_from_stage_outputs({})
    assert signals == {}


def test_extract_signals_mixed_outputs():
    structured_outputs = {
        "verify": {"build_success": True, "lint_clean": True},
        "test": {"test_pass_rate": 1.0},
    }
    signals = extract_signals_from_stage_outputs(structured_outputs)
    assert signals["build_success"] is True
    assert signals["lint_clean"] is True
    assert signals["test_pass_rate"] == 1.0


def test_evaluation_result_dataclass():
    result = EvaluationResult(
        composite_score=0.85,
        llm_confidence=0.9,
        external_signals={"test_pass_rate": 0.8},
        passed=True,
        details="All checks passed",
    )
    assert result.composite_score == 0.85
    assert result.passed is True
