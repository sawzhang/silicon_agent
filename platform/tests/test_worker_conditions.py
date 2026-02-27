from __future__ import annotations

import pytest

from app.worker import conditions


@pytest.mark.parametrize(
    ("condition", "outputs", "expected"),
    [
        ({"source_stage": "code", "field": "status", "value": "pass"}, {"code": {"status": "pass"}}, True),
        ({"source_stage": "code", "field": "status", "operator": "ne", "value": "fail"}, {"code": {"status": "pass"}}, True),
        ({"source_stage": "code", "field": "score", "operator": "gt", "value": 80}, {"code": {"score": 90}}, True),
        ({"source_stage": "code", "field": "score", "operator": "lt", "value": 80}, {"code": {"score": 70}}, True),
        ({"source_stage": "code", "field": "score", "operator": "gte", "value": 80}, {"code": {"score": 80}}, True),
        ({"source_stage": "code", "field": "score", "operator": "lte", "value": 80}, {"code": {"score": 80}}, True),
        ({"source_stage": "code", "field": "message", "operator": "contains", "value": "ok"}, {"code": {"message": "all ok"}}, True),
        ({"source_stage": "code", "field": "items", "operator": "contains", "value": "a"}, {"code": {"items": ["a", "b"]}}, True),
        ({"source_stage": "code", "field": "items", "operator": "not_contains", "value": "c"}, {"code": {"items": ["a", "b"]}}, True),
        ({"source_stage": "code", "field": "meta.token", "operator": "exists"}, {"code": {"meta": {"token": "x"}}}, True),
        ({"source_stage": "code", "field": "meta.token", "operator": "not_exists"}, {"code": {"meta": {}}}, True),
    ],
)
def test_evaluate_condition_operators(monkeypatch: pytest.MonkeyPatch, condition, outputs, expected):
    monkeypatch.setattr(conditions.settings, "CONDITIONS_ENABLED", True)
    assert conditions.evaluate_condition(condition, outputs) is expected


def test_evaluate_condition_disabled_returns_true(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(conditions.settings, "CONDITIONS_ENABLED", False)
    assert conditions.evaluate_condition({}, {}) is True


def test_evaluate_condition_invalid_inputs_default_to_execute(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(conditions.settings, "CONDITIONS_ENABLED", True)

    assert conditions.evaluate_condition({"field": "a"}, {}) is True
    assert conditions.evaluate_condition({"source_stage": "code"}, {}) is True
    assert conditions.evaluate_condition(
        {"source_stage": "code", "field": "status", "operator": "unknown"},
        {"code": {"status": "ok"}},
    ) is True
    assert conditions.evaluate_condition(
        {"source_stage": "missing", "field": "status", "value": "ok"},
        {"code": {"status": "ok"}},
    ) is True


def test_get_nested_field_and_numeric_compare_helpers():
    data = {"a": {"b": {"c": 1}}, "k": "v"}
    assert conditions._get_nested_field(data, "a.b.c") == 1
    assert conditions._get_nested_field(data, "a.x.c") is None
    assert conditions._get_nested_field(data, "k.c") is None

    assert conditions._numeric_compare("3", "2", lambda a, b: a > b) is True
    assert conditions._numeric_compare("x", "2", lambda a, b: a > b) is False


def test_apply_operator_edge_cases():
    assert conditions._apply_operator("eq", None, "x") is False
    assert conditions._apply_operator("contains", 123, "1") is False
    assert conditions._apply_operator("not_contains", 123, "1") is True
    assert conditions._apply_operator("anything", "x", "y") is True
