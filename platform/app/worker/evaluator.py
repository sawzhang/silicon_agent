"""External evaluator: composite scoring from LLM confidence + external signals."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from app.config import settings

logger = logging.getLogger(__name__)

DEFAULT_WEIGHTS: Dict[str, float] = {
    "llm_confidence": 0.3,
    "test_pass_rate": 0.4,
    "build_success": 0.2,
    "lint_clean": 0.1,
}


@dataclass
class EvaluationResult:
    composite_score: float  # 0.0 - 1.0
    llm_confidence: float
    external_signals: Dict[str, Any] = field(default_factory=dict)
    passed: bool = False
    details: str = ""


def _load_weights() -> Dict[str, float]:
    """Load composite weights from config, falling back to defaults."""
    try:
        parsed = json.loads(settings.EVALUATOR_COMPOSITE_WEIGHTS)
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, TypeError):
        pass
    return dict(DEFAULT_WEIGHTS)


def compute_composite_score(
    llm_confidence: float,
    external_signals: Dict[str, Any],
    weights: Optional[Dict[str, float]] = None,
) -> float:
    """Compute weighted composite score from LLM confidence and external signals.

    Args:
        llm_confidence: LLM self-assessed confidence (0.0 - 1.0).
        external_signals: Dict with optional keys: test_pass_rate (float 0-1),
            build_success (bool), lint_clean (bool).
        weights: Optional custom weights. If None, uses config/defaults.

    Returns:
        Composite score between 0.0 and 1.0.
    """
    if weights is None:
        weights = _load_weights()

    # If no external signals, fall back to pure LLM confidence
    if not external_signals:
        return max(0.0, min(1.0, llm_confidence))

    score = 0.0
    total_weight = 0.0

    # LLM confidence
    w = weights.get("llm_confidence", 0.3)
    score += w * max(0.0, min(1.0, llm_confidence))
    total_weight += w

    # Test pass rate (float 0-1)
    if "test_pass_rate" in external_signals:
        w = weights.get("test_pass_rate", 0.4)
        rate = float(external_signals["test_pass_rate"])
        score += w * max(0.0, min(1.0, rate))
        total_weight += w

    # Build success (bool → 1.0 or 0.0)
    if "build_success" in external_signals:
        w = weights.get("build_success", 0.2)
        score += w * (1.0 if external_signals["build_success"] else 0.0)
        total_weight += w

    # Lint clean (bool → 1.0 or 0.0)
    if "lint_clean" in external_signals:
        w = weights.get("lint_clean", 0.1)
        score += w * (1.0 if external_signals["lint_clean"] else 0.0)
        total_weight += w

    if total_weight <= 0:
        return max(0.0, min(1.0, llm_confidence))

    return max(0.0, min(1.0, score / total_weight * (sum(weights.values()) / total_weight)
                        if total_weight != sum(weights.values()) else score))


def extract_signals_from_stage_outputs(
    structured_outputs: Dict[str, dict],
) -> Dict[str, Any]:
    """Extract external signals from verify/test stage structured outputs.

    Looks for known keys in stage output_structured fields:
    - verify stage: build_success (bool), lint_clean (bool)
    - test stage: test_pass_rate (float), tests_passed (int), tests_total (int)
    """
    signals: Dict[str, Any] = {}

    # Extract from verify stage
    verify_out = structured_outputs.get("verify", {})
    if isinstance(verify_out, dict):
        if "build_success" in verify_out:
            signals["build_success"] = bool(verify_out["build_success"])
        if "lint_clean" in verify_out:
            signals["lint_clean"] = bool(verify_out["lint_clean"])

    # Extract from test stage
    test_out = structured_outputs.get("test", {})
    if isinstance(test_out, dict):
        if "test_pass_rate" in test_out:
            signals["test_pass_rate"] = float(test_out["test_pass_rate"])
        elif "tests_passed" in test_out and "tests_total" in test_out:
            total = int(test_out["tests_total"])
            if total > 0:
                signals["test_pass_rate"] = int(test_out["tests_passed"]) / total

    return signals
