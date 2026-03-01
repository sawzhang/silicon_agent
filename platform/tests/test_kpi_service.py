"""Mock-session unit tests for KPIService.

All session.execute() calls are mocked so every line after `await` is
covered within the same trace context — fixing the coverage.py / Python 3.13
sys.monitoring coroutine-resume blind-spot.

Covered uncovered lines:
  kpi_service.py: 47-54, 61-87, 118-122, 135-162, 187-198, 213-294, 321-371
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.kpi_service import KPIService


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_session() -> AsyncMock:
    """Return an AsyncMock that behaves like an AsyncSession."""
    session = AsyncMock()
    return session


def _exec_result(*, scalar=None, scalars_list=None, one_row=None):
    """Build a mock object returned by session.execute()."""
    result = MagicMock()
    result.scalar.return_value = scalar
    result.scalar_one_or_none.return_value = scalar

    if scalars_list is not None:
        sc = MagicMock()
        sc.all.return_value = scalars_list
        result.scalars.return_value = sc

    if one_row is not None:
        result.one.return_value = one_row

    # .all() on the result itself (for role_result.all() in get_roi_summary)
    result.all.return_value = scalars_list or []

    return result


def _make_kpi_metric(
    *,
    metric_name: str = "tokens_used",
    value: float = 100.0,
    unit: str = "tokens",
    agent_role: str = "coding",
    recorded_at: datetime | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        metric_name=metric_name,
        value=value,
        unit=unit,
        agent_role=agent_role,
        recorded_at=recorded_at or datetime.now(timezone.utc),
    )


def _make_task(
    *,
    id: str = "t-1",
    title: str = "Task",
    status: str = "completed",
    total_cost_rmb: float = 0.5,
    total_tokens: int = 10000,
    created_at: datetime | None = None,
    completed_at: datetime | None = None,
    template=None,
    project=None,
    stages: list | None = None,
) -> SimpleNamespace:
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=id,
        title=title,
        status=status,
        total_cost_rmb=total_cost_rmb,
        total_tokens=total_tokens,
        created_at=created_at or now - timedelta(hours=2),
        completed_at=completed_at or now - timedelta(hours=1),
        template=template,
        project=project,
        stages=stages or [],
        project_id=None,
    )


def _make_gate(
    *,
    id: str = "g-1",
    gate_type: str = "review",
    task_id: str = "t-1",
    agent_role: str = "review",
    status: str = "pending",
    created_at: datetime | None = None,
    reviewed_at: datetime | None = None,
    content: dict | None = None,
    retry_count: int = 0,
    is_dynamic: bool = False,
    revised_content: str | None = None,
    reviewer: str | None = None,
    review_comment: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=id,
        gate_type=gate_type,
        task_id=task_id,
        agent_role=agent_role,
        status=status,
        created_at=created_at or datetime.now(timezone.utc),
        reviewed_at=reviewed_at,
        content=content,
        retry_count=retry_count,
        is_dynamic=is_dynamic,
        revised_content=revised_content,
        reviewer=reviewer,
        review_comment=review_comment,
    )


# ── get_summary ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_summary_empty_db():
    """Lines 47-87: get_summary when all aggregates return 0/None."""
    session = _make_session()
    session.execute.side_effect = [
        _exec_result(scalar=0),    # total tasks count
        _exec_result(scalar=0),    # completed tasks count
        _exec_result(scalar=None), # total tokens  → falls back to 0
        _exec_result(scalar=None), # total cost    → falls back to 0.0
        _exec_result(scalars_list=[]),  # KPI metrics list
        _exec_result(scalar=None), # avg duration  → falls back to 0.0
    ]

    svc = KPIService(session)
    result = await svc.get_summary()

    assert result.total_tasks == 0
    assert result.completed_tasks == 0
    assert result.success_rate == 0.0
    assert result.avg_duration_minutes == 0.0
    assert result.total_tokens == 0
    assert result.total_cost_rmb == 0.0
    assert result.metrics == []


@pytest.mark.asyncio
async def test_get_summary_with_tasks():
    """Lines 47-87: get_summary with realistic data, success_rate computed."""
    session = _make_session()
    metric = _make_kpi_metric()
    session.execute.side_effect = [
        _exec_result(scalar=10),         # total tasks
        _exec_result(scalar=8),          # completed tasks
        _exec_result(scalar=500000),     # total tokens
        _exec_result(scalar=5.0),        # total cost
        _exec_result(scalars_list=[metric]),  # KPI metrics
        _exec_result(scalar=0.0417),     # avg duration (julianday diff ~ 1h)
    ]

    svc = KPIService(session)
    result = await svc.get_summary()

    assert result.total_tasks == 10
    assert result.completed_tasks == 8
    assert result.success_rate == 80.0   # 8/10 * 100
    assert result.total_tokens == 500000
    assert result.total_cost_rmb == 5.0
    assert len(result.metrics) == 1
    assert result.metrics[0].metric_name == "tokens_used"
    # avg_duration_minutes = 0.0417 * 24 * 60 ≈ 60.05
    assert result.avg_duration_minutes > 0


@pytest.mark.asyncio
async def test_get_summary_success_rate_exact():
    """Line 54: success_rate = completed/total * 100 rounded to 2 dp."""
    session = _make_session()
    session.execute.side_effect = [
        _exec_result(scalar=3),
        _exec_result(scalar=1),
        _exec_result(scalar=0),
        _exec_result(scalar=0.0),
        _exec_result(scalars_list=[]),
        _exec_result(scalar=None),
    ]

    svc = KPIService(session)
    result = await svc.get_summary()
    assert result.success_rate == round(1 / 3 * 100, 2)


@pytest.mark.asyncio
async def test_get_summary_multiple_metrics():
    """Lines 66-103: metrics list populated from KPIMetricModel objects."""
    now = datetime.now(timezone.utc)
    metrics = [
        _make_kpi_metric(metric_name="tokens_used", value=100.0, recorded_at=now),
        _make_kpi_metric(metric_name="duration_s", value=30.0, unit="seconds", recorded_at=now),
    ]
    session = _make_session()
    session.execute.side_effect = [
        _exec_result(scalar=2),
        _exec_result(scalar=2),
        _exec_result(scalar=1000),
        _exec_result(scalar=0.1),
        _exec_result(scalars_list=metrics),
        _exec_result(scalar=0.002),
    ]

    svc = KPIService(session)
    result = await svc.get_summary()
    assert len(result.metrics) == 2
    names = {m.metric_name for m in result.metrics}
    assert names == {"tokens_used", "duration_s"}


# ── get_timeseries ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_timeseries_empty():
    """Lines 118-122: empty metrics list → unit defaults to 'count'."""
    session = _make_session()
    session.execute.return_value = _exec_result(scalars_list=[])

    svc = KPIService(session)
    result = await svc.get_timeseries("nonexistent_metric")

    assert result.metric_name == "nonexistent_metric"
    assert result.unit == "count"
    assert result.data == []


@pytest.mark.asyncio
async def test_get_timeseries_with_data():
    """Lines 117-128: non-empty metrics → unit from first record, data points built."""
    now = datetime.now(timezone.utc)
    m1 = _make_kpi_metric(metric_name="tokens_used", value=100.0, unit="tokens", recorded_at=now - timedelta(hours=2))
    m2 = _make_kpi_metric(metric_name="tokens_used", value=200.0, unit="tokens", recorded_at=now - timedelta(hours=1))
    session = _make_session()
    session.execute.return_value = _exec_result(scalars_list=[m1, m2])

    svc = KPIService(session)
    result = await svc.get_timeseries("tokens_used")

    assert result.metric_name == "tokens_used"
    assert result.unit == "tokens"
    assert len(result.data) == 2
    assert result.data[0].value == 100.0
    assert result.data[1].value == 200.0


@pytest.mark.asyncio
async def test_get_timeseries_with_agent_role_filter():
    """Line 115: agent_role kwarg causes extra .where() on query (no exception)."""
    session = _make_session()
    session.execute.return_value = _exec_result(scalars_list=[])

    svc = KPIService(session)
    result = await svc.get_timeseries("tokens_used", agent_role="coding")

    assert result.metric_name == "tokens_used"
    assert result.unit == "count"
    assert result.data == []


# ── generate_report ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_report_all_roles():
    """Lines 135-162: generate_report calls get_summary + 7 role queries."""
    session = _make_session()

    # get_summary makes 6 calls; then generate_report makes 7 (one per role)
    summary_calls = [
        _exec_result(scalar=5),
        _exec_result(scalar=4),
        _exec_result(scalar=200000),
        _exec_result(scalar=2.0),
        _exec_result(scalars_list=[]),
        _exec_result(scalar=None),
    ]

    roles = ["orchestrator", "spec", "coding", "test", "review", "smoke", "doc"]
    role_calls = []
    for i, role in enumerate(roles):
        # Returns a row with (count, sum_tokens, avg_secs)
        row = SimpleNamespace(**{"0": i + 1, "1": (i + 1) * 1000, "2": (i + 1) * 10.0})
        # one() returns a tuple-like object
        result = MagicMock()
        result.one.return_value = ((i + 1), (i + 1) * 1000, (i + 1) * 10.0)
        role_calls.append(result)

    session.execute.side_effect = summary_calls + role_calls

    svc = KPIService(session)
    report = await svc.generate_report(period="weekly")

    assert report.period == "weekly"
    assert report.summary.total_tasks == 5
    assert report.summary.completed_tasks == 4
    assert len(report.by_agent) == 7
    for role in roles:
        assert role in report.by_agent
        agent_summary = report.by_agent[role]
        assert agent_summary.total_tasks >= 1
        assert agent_summary.avg_duration_minutes >= 0


@pytest.mark.asyncio
async def test_generate_report_default_period():
    """Line 131: period defaults to 'daily'."""
    session = _make_session()
    summary_calls = [
        _exec_result(scalar=0),
        _exec_result(scalar=0),
        _exec_result(scalar=None),
        _exec_result(scalar=None),
        _exec_result(scalars_list=[]),
        _exec_result(scalar=None),
    ]
    role_calls = []
    for _ in range(7):
        result = MagicMock()
        result.one.return_value = (0, None, None)
        role_calls.append(result)

    session.execute.side_effect = summary_calls + role_calls
    svc = KPIService(session)
    report = await svc.generate_report()

    assert report.period == "daily"


@pytest.mark.asyncio
async def test_generate_report_role_token_computation():
    """Lines 148-160: per-role KPISummaryResponse built from stage aggregates."""
    session = _make_session()
    summary_calls = [
        _exec_result(scalar=2),
        _exec_result(scalar=2),
        _exec_result(scalar=100000),
        _exec_result(scalar=1.0),
        _exec_result(scalars_list=[]),
        _exec_result(scalar=None),
    ]
    # coding role has 3 stages, 90000 tokens, 180s avg
    role_calls = []
    for i, role in enumerate(["orchestrator", "spec", "coding", "test", "review", "smoke", "doc"]):
        result = MagicMock()
        if role == "coding":
            result.one.return_value = (3, 90000, 180.0)
        else:
            result.one.return_value = (0, None, None)
        role_calls.append(result)

    session.execute.side_effect = summary_calls + role_calls
    svc = KPIService(session)
    report = await svc.generate_report()

    coding = report.by_agent["coding"]
    assert coding.total_tasks == 3
    assert coding.total_tokens == 90000
    assert coding.avg_duration_minutes == round(180.0 / 60, 2)


# ── compare ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_compare_all_roles_default():
    """Lines 187-198: compare with no roles arg iterates all 7 roles."""
    session = _make_session()

    now = datetime.now(timezone.utc)
    coding_metric = _make_kpi_metric(metric_name="tokens_used", agent_role="coding", recorded_at=now)

    all_roles = ["orchestrator", "spec", "coding", "test", "review", "smoke", "doc"]
    side_effects = []
    for role in all_roles:
        if role == "coding":
            side_effects.append(_exec_result(scalars_list=[coding_metric]))
        else:
            side_effects.append(_exec_result(scalars_list=[]))

    session.execute.side_effect = side_effects
    svc = KPIService(session)
    result = await svc.compare("tokens_used")

    assert set(result.keys()) == set(all_roles)
    assert len(result["coding"]) == 1
    assert result["coding"][0].metric_name == "tokens_used"
    for role in all_roles:
        if role != "coding":
            assert result[role] == []


@pytest.mark.asyncio
async def test_compare_explicit_roles():
    """Lines 172-198: compare with explicit roles list."""
    session = _make_session()
    now = datetime.now(timezone.utc)
    m = _make_kpi_metric(metric_name="duration_s", agent_role="test", unit="seconds", recorded_at=now)

    session.execute.side_effect = [
        _exec_result(scalars_list=[]),   # coding — empty
        _exec_result(scalars_list=[m]),  # test — 1 record
    ]

    svc = KPIService(session)
    result = await svc.compare("duration_s", roles=["coding", "test"])

    assert set(result.keys()) == {"coding", "test"}
    assert result["coding"] == []
    assert len(result["test"]) == 1
    assert result["test"][0].agent_role == "test"


@pytest.mark.asyncio
async def test_compare_builds_kpi_metric_value_objects():
    """Lines 188-197: KPIMetricValue objects constructed correctly from ORM objects."""
    now = datetime.now(timezone.utc)
    m = _make_kpi_metric(
        metric_name="success_rate",
        value=95.0,
        unit="percent",
        agent_role="review",
        recorded_at=now,
    )
    session = _make_session()
    session.execute.side_effect = [_exec_result(scalars_list=[m])]

    svc = KPIService(session)
    result = await svc.compare("success_rate", roles=["review"])

    kv = result["review"][0]
    assert kv.metric_name == "success_rate"
    assert kv.value == 95.0
    assert kv.unit == "percent"
    assert kv.agent_role == "review"
    assert kv.recorded_at == now


# ── get_roi_summary ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_roi_summary_empty():
    """Lines 213-294: get_roi_summary with no completed tasks."""
    session = _make_session()

    # First execute: completed tasks list (empty)
    # Second execute: role aggregation (empty rows)
    tasks_result = _exec_result(scalars_list=[])
    role_result = MagicMock()
    role_result.all.return_value = []
    session.execute.side_effect = [tasks_result, role_result]

    svc = KPIService(session)
    result = await svc.get_roi_summary(days=30)

    assert result.total_tasks_completed == 0
    assert result.total_agent_cost_rmb == 0.0
    assert result.total_estimated_manual_rmb == 0.0
    assert result.total_savings_rmb == 0.0
    assert result.roi_ratio == 0.0
    assert result.by_role == []
    assert result.recent_tasks == []


@pytest.mark.asyncio
async def test_get_roi_summary_with_tasks_no_template():
    """Lines 224-258: tasks without template use global hours_per_task."""
    now = datetime.now(timezone.utc)
    task = _make_task(
        id="t-1",
        title="No Template Task",
        total_cost_rmb=0.5,
        created_at=now - timedelta(hours=2),
        completed_at=now - timedelta(hours=1),
        template=None,
    )

    session = _make_session()
    tasks_result = _exec_result(scalars_list=[task])
    role_result = MagicMock()
    role_result.all.return_value = []
    session.execute.side_effect = [tasks_result, role_result]

    svc = KPIService(session)
    # settings.ESTIMATED_HOURS_PER_TASK = 8.0, HOURLY_RATE_RMB = 150.0
    result = await svc.get_roi_summary(days=30)

    assert result.total_tasks_completed == 1
    assert len(result.recent_tasks) == 1
    breakdown = result.recent_tasks[0]
    assert breakdown.task_id == "t-1"
    assert breakdown.estimated_manual_hours == 8.0
    assert breakdown.estimated_manual_rmb == 8.0 * 150.0
    assert breakdown.agent_cost_rmb == 0.5
    assert breakdown.savings_rmb == round(8.0 * 150.0 - 0.5, 2)
    # duration ~60 min
    assert 50 < breakdown.agent_duration_minutes < 70


@pytest.mark.asyncio
async def test_get_roi_summary_with_template_hours():
    """Lines 226-229: tasks with template use template.estimated_hours."""
    now = datetime.now(timezone.utc)
    template = SimpleNamespace(estimated_hours=4.0)
    task = _make_task(
        id="t-2",
        title="Template Task",
        total_cost_rmb=0.2,
        created_at=now - timedelta(hours=3),
        completed_at=now - timedelta(hours=1),
        template=template,
    )

    session = _make_session()
    tasks_result = _exec_result(scalars_list=[task])
    role_result = MagicMock()
    role_result.all.return_value = []
    session.execute.side_effect = [tasks_result, role_result]

    svc = KPIService(session)
    result = await svc.get_roi_summary()

    breakdown = result.recent_tasks[0]
    assert breakdown.estimated_manual_hours == 4.0
    assert breakdown.estimated_manual_rmb == 4.0 * 150.0


@pytest.mark.asyncio
async def test_get_roi_summary_no_timestamps():
    """Lines 234-237: task with no completed_at → duration_min = 0.0."""
    task = _make_task(
        id="t-3",
        total_cost_rmb=0.1,
        created_at=None,
        completed_at=None,
        template=None,
    )
    task.created_at = None
    task.completed_at = None

    session = _make_session()
    tasks_result = _exec_result(scalars_list=[task])
    role_result = MagicMock()
    role_result.all.return_value = []
    session.execute.side_effect = [tasks_result, role_result]

    svc = KPIService(session)
    result = await svc.get_roi_summary()

    assert result.recent_tasks[0].agent_duration_minutes == 0.0


@pytest.mark.asyncio
async def test_get_roi_summary_roi_ratio_with_cost():
    """Lines 259: roi_ratio = savings / agent_cost."""
    now = datetime.now(timezone.utc)
    task = _make_task(
        total_cost_rmb=1.0,
        created_at=now - timedelta(hours=1),
        completed_at=now,
        template=None,
    )

    session = _make_session()
    tasks_result = _exec_result(scalars_list=[task])
    role_result = MagicMock()
    role_result.all.return_value = []
    session.execute.side_effect = [tasks_result, role_result]

    svc = KPIService(session)
    result = await svc.get_roi_summary()

    # total_estimated_manual = 8 * 150 = 1200, total_agent_cost = 1.0
    # savings = 1199, roi_ratio = 1199 / 1.0 = 1199.0
    expected_ratio = round((8 * 150.0 - 1.0) / 1.0, 2)
    assert result.roi_ratio == expected_ratio


@pytest.mark.asyncio
async def test_get_roi_summary_by_role_efficiency():
    """Lines 264-292: by_role filled from stage aggregation query rows."""
    now = datetime.now(timezone.utc)
    task = _make_task(total_cost_rmb=0.5, template=None,
                      created_at=now - timedelta(hours=2), completed_at=now - timedelta(hours=1))

    coding_row = SimpleNamespace(
        agent_role="coding",
        total_stages=5,
        total_tokens=50000,
        avg_duration=120.0,
    )
    test_row = SimpleNamespace(
        agent_role="test",
        total_stages=3,
        total_tokens=None,  # NULL → 0
        avg_duration=None,  # NULL → 0
    )

    session = _make_session()
    tasks_result = _exec_result(scalars_list=[task])
    role_result = MagicMock()
    role_result.all.return_value = [coding_row, test_row]
    session.execute.side_effect = [tasks_result, role_result]

    svc = KPIService(session)
    result = await svc.get_roi_summary()

    assert len(result.by_role) == 2
    coding = next(r for r in result.by_role if r.role == "coding")
    assert coding.display_name == "Coding Agent"
    assert coding.total_stages == 5
    assert coding.total_tokens == 50000
    assert coding.avg_duration_seconds == 120.0

    test_role = next(r for r in result.by_role if r.role == "test")
    assert test_role.total_tokens == 0
    assert test_role.avg_duration_seconds == 0.0


@pytest.mark.asyncio
async def test_get_roi_summary_recent_tasks_capped_at_20():
    """Lines 244-255: recent_tasks only contains first 20 tasks."""
    now = datetime.now(timezone.utc)
    tasks = [
        _make_task(id=f"t-{i}", total_cost_rmb=0.1,
                   created_at=now - timedelta(hours=2), completed_at=now - timedelta(hours=1))
        for i in range(25)
    ]

    session = _make_session()
    tasks_result = _exec_result(scalars_list=tasks)
    role_result = MagicMock()
    role_result.all.return_value = []
    session.execute.side_effect = [tasks_result, role_result]

    svc = KPIService(session)
    result = await svc.get_roi_summary()

    assert result.total_tasks_completed == 25
    assert len(result.recent_tasks) == 20


# ── get_cockpit ────────────────────────────────────────────────────────────────


def _gate_ns(id: str = "g-1", gate_type: str = "review", task_id: str = "t-1",
             agent_role: str = "review", status: str = "pending",
             content: dict | None = None, retry_count: int = 0,
             is_dynamic: bool = False, revised_content=None,
             reviewer=None, review_comment=None,
             created_at: datetime | None = None,
             reviewed_at: datetime | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=id, gate_type=gate_type, task_id=task_id, agent_role=agent_role,
        status=status, content=content, retry_count=retry_count,
        is_dynamic=is_dynamic, revised_content=revised_content,
        reviewer=reviewer, review_comment=review_comment,
        created_at=created_at or datetime.now(timezone.utc),
        reviewed_at=reviewed_at,
    )


@pytest.mark.asyncio
async def test_get_cockpit_empty():
    """Lines 321-371: get_cockpit with all empty results."""
    session = _make_session()
    session.execute.side_effect = [
        _exec_result(scalars_list=[]),  # pending gates
        _exec_result(scalars_list=[]),  # running tasks
        _exec_result(scalars_list=[]),  # failed tasks today
        _exec_result(scalars_list=[]),  # recent completed
        _exec_result(scalar=0),         # completed_today count
    ]

    svc = KPIService(session)
    result = await svc.get_cockpit()

    assert result.pending_gates_count == 0
    assert result.running_tasks_count == 0
    assert result.failed_tasks_today == 0
    assert result.completed_tasks_today == 0
    assert result.pending_gates == []
    assert result.running_tasks == []
    assert result.failed_tasks == []
    assert result.recent_completed == []


@pytest.mark.asyncio
async def test_get_cockpit_with_pending_gates():
    """Lines 316-323: pending gates fetched and converted to GateDetailResponse."""
    from app.schemas.gate import GateDetailResponse

    now = datetime.now(timezone.utc)
    gate_obj = _gate_ns(id="g-1", status="pending", created_at=now)

    # Patch model_validate so it doesn't require real ORM object
    with patch.object(GateDetailResponse, "model_validate", side_effect=lambda g: GateDetailResponse(
        id=g.id,
        gate_type=g.gate_type,
        task_id=g.task_id,
        agent_role=g.agent_role,
        status=g.status,
        created_at=g.created_at,
    )):
        session = _make_session()
        session.execute.side_effect = [
            _exec_result(scalars_list=[gate_obj]),  # pending gates
            _exec_result(scalars_list=[]),           # running tasks
            _exec_result(scalars_list=[]),           # failed today
            _exec_result(scalars_list=[]),           # recent completed
            _exec_result(scalar=0),                  # count today
        ]

        svc = KPIService(session)
        result = await svc.get_cockpit()

    assert result.pending_gates_count == 1
    assert result.pending_gates[0].id == "g-1"
    assert result.pending_gates[0].status == "pending"


@pytest.mark.asyncio
async def test_get_cockpit_running_task_with_stage():
    """Lines 327-334: running task's current_stage identified from running stage."""
    now = datetime.now(timezone.utc)
    running_stage = SimpleNamespace(
        status="running",
        stage_name="coding",
        error_message=None,
    )
    running_task = _make_task(
        id="t-run-1",
        status="running",
        stages=[running_stage],
        completed_at=None,
    )
    running_task.project = None
    running_task.template = None

    session = _make_session()
    session.execute.side_effect = [
        _exec_result(scalars_list=[]),             # pending gates
        _exec_result(scalars_list=[running_task]), # running tasks
        _exec_result(scalars_list=[]),             # failed today
        _exec_result(scalars_list=[]),             # recent completed
        _exec_result(scalar=0),                    # count today
    ]

    svc = KPIService(session)
    result = await svc.get_cockpit()

    assert result.running_tasks_count == 1
    task_item = result.running_tasks[0]
    assert task_item.id == "t-run-1"
    assert task_item.current_stage == "coding"
    assert task_item.error_message is None


@pytest.mark.asyncio
async def test_get_cockpit_failed_task_with_error_message():
    """Lines 337-347: failed task exposes error_message from failed stage."""
    now = datetime.now(timezone.utc)
    failed_stage = SimpleNamespace(
        status="failed",
        stage_name="test",
        error_message="Assertion failed: 3 tests",
    )
    failed_task = _make_task(
        id="t-fail-1",
        status="failed",
        stages=[failed_stage],
        completed_at=now - timedelta(minutes=10),
    )
    failed_task.project = None
    failed_task.template = None

    session = _make_session()
    session.execute.side_effect = [
        _exec_result(scalars_list=[]),           # pending gates
        _exec_result(scalars_list=[]),           # running tasks
        _exec_result(scalars_list=[failed_task]), # failed today
        _exec_result(scalars_list=[]),           # recent completed
        _exec_result(scalar=0),                  # count today
    ]

    svc = KPIService(session)
    result = await svc.get_cockpit()

    assert result.failed_tasks_today == 1
    task_item = result.failed_tasks[0]
    assert task_item.id == "t-fail-1"
    assert task_item.error_message == "Assertion failed: 3 tests"


@pytest.mark.asyncio
async def test_get_cockpit_recent_completed():
    """Lines 350-358: recently completed tasks listed."""
    now = datetime.now(timezone.utc)
    comp_task = _make_task(
        id="t-comp-1",
        status="completed",
        stages=[],
        completed_at=now - timedelta(hours=1),
    )
    comp_task.project = None
    comp_task.template = None

    session = _make_session()
    session.execute.side_effect = [
        _exec_result(scalars_list=[]),            # pending gates
        _exec_result(scalars_list=[]),            # running tasks
        _exec_result(scalars_list=[]),            # failed today
        _exec_result(scalars_list=[comp_task]),   # recent completed
        _exec_result(scalar=1),                   # count today
    ]

    svc = KPIService(session)
    result = await svc.get_cockpit()

    assert result.completed_tasks_today == 1
    assert len(result.recent_completed) == 1
    assert result.recent_completed[0].id == "t-comp-1"


@pytest.mark.asyncio
async def test_get_cockpit_completed_today_count():
    """Lines 361-369: completed_tasks_today from scalar count query."""
    session = _make_session()
    session.execute.side_effect = [
        _exec_result(scalars_list=[]),
        _exec_result(scalars_list=[]),
        _exec_result(scalars_list=[]),
        _exec_result(scalars_list=[]),
        _exec_result(scalar=7),   # completed today = 7
    ]

    svc = KPIService(session)
    result = await svc.get_cockpit()

    assert result.completed_tasks_today == 7


# ── _task_to_cockpit_item (static method) ────────────────────────────────────


def test_task_to_cockpit_item_with_project_and_template():
    """Lines 383-403: _task_to_cockpit_item extracts all fields correctly."""
    now = datetime.now(timezone.utc)
    project = SimpleNamespace(name="My Project")
    template = SimpleNamespace(name="full_pipeline")
    running_stage = SimpleNamespace(status="running", stage_name="coding", error_message=None)
    failed_stage = SimpleNamespace(status="failed", stage_name="test", error_message="boom")
    task = SimpleNamespace(
        id="t-x",
        title="Test Task",
        status="running",
        project=project,
        template=template,
        created_at=now - timedelta(hours=1),
        completed_at=None,
        stages=[running_stage, failed_stage],
        total_tokens=5000,
        total_cost_rmb=0.05,
    )

    item = KPIService._task_to_cockpit_item(task)

    assert item.id == "t-x"
    assert item.title == "Test Task"
    assert item.status == "running"
    assert item.project_name == "My Project"
    assert item.template_name == "full_pipeline"
    assert item.current_stage == "coding"
    assert item.error_message == "boom"
    assert item.total_tokens == 5000
    assert item.total_cost_rmb == 0.05


def test_task_to_cockpit_item_no_project_no_template():
    """Lines 395-396: project_name and template_name are None when not set."""
    now = datetime.now(timezone.utc)
    task = SimpleNamespace(
        id="t-y",
        title="Task Y",
        status="completed",
        project=None,
        template=None,
        created_at=now - timedelta(hours=2),
        completed_at=now,
        stages=[],
        total_tokens=None,
        total_cost_rmb=None,
    )

    item = KPIService._task_to_cockpit_item(task)

    assert item.project_name is None
    assert item.template_name is None
    assert item.current_stage is None
    assert item.error_message is None
    assert item.total_tokens == 0      # None → 0
    assert item.total_cost_rmb == 0.0  # None → 0.0


def test_task_to_cockpit_item_multiple_running_stages_last_wins():
    """Lines 387-389: if multiple running stages, current_stage = last one seen."""
    now = datetime.now(timezone.utc)
    s1 = SimpleNamespace(status="running", stage_name="spec", error_message=None)
    s2 = SimpleNamespace(status="running", stage_name="coding", error_message=None)
    task = SimpleNamespace(
        id="t-z",
        title="Task Z",
        status="running",
        project=None,
        template=None,
        created_at=now,
        completed_at=None,
        stages=[s1, s2],
        total_tokens=0,
        total_cost_rmb=0.0,
    )

    item = KPIService._task_to_cockpit_item(task)
    # Both stages are "running" — last one wins (coding)
    assert item.current_stage == "coding"
