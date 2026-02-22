"""Tests for the stage output compressor (fallback mode, no LLM)."""
import pytest

from app.worker.compressor import (
    CompressedOutput,
    CompressionResult,
    _fallback_l0,
    _fallback_l1,
    compress_stage_output,
)


def test_fallback_l0_short():
    assert _fallback_l0("Hello world") == "Hello world"


def test_fallback_l0_multiline():
    text = "First line\nSecond line\nThird line"
    assert _fallback_l0(text) == "First line"


def test_fallback_l1_short():
    text = "Short text"
    assert _fallback_l1(text) == "Short text"


def test_fallback_l1_truncates():
    text = "x" * 2000
    result = _fallback_l1(text)
    assert result.endswith("\n...")
    assert len(result) < 2000


def test_compression_result_sliding_window():
    cr = CompressionResult()
    for i in range(4):
        cr.add(CompressedOutput(
            stage_name=f"stage_{i}",
            l0=f"l0_{i}",
            l1=f"l1_{i}",
            l2=f"l2_{i}_full_content",
        ))

    # Building context for stage_index=4 (the 5th stage)
    ctx = cr.build_prior_context(4)
    assert len(ctx) == 4

    # stage_0, stage_1 → distance ≥ 2 → L0
    assert ctx[0]["output"].startswith("[概要]")
    assert ctx[1]["output"].startswith("[概要]")

    # stage_2 → distance 1 → L1
    assert ctx[2]["output"].startswith("[摘要]")

    # stage_3 → distance 0 → L2 full text
    assert ctx[3]["output"] == "l2_3_full_content"


@pytest.mark.asyncio
async def test_compress_stage_output_fallback():
    """When compression is disabled, should use fallback."""
    co = await compress_stage_output("test_stage", "This is the output text")
    assert co.stage_name == "test_stage"
    assert co.l2 == "This is the output text"
    assert co.l0  # should have some fallback content
    assert co.l1  # should have some fallback content
