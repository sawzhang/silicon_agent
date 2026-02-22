"""Tests for KPI API endpoints."""
import pytest


@pytest.mark.asyncio
async def test_kpi_summary(client):
    """GET /api/v1/kpi/summary returns 200 with expected structure."""
    resp = await client.get("/api/v1/kpi/summary")
    assert resp.status_code == 200
    data = resp.json()
    assert "total_tasks" in data
    assert "completed_tasks" in data
    assert "success_rate" in data
    assert "total_tokens" in data
    assert "total_cost_rmb" in data


@pytest.mark.asyncio
async def test_kpi_metrics(client):
    """GET /api/v1/kpi/metrics/tokens_used returns 200."""
    resp = await client.get("/api/v1/kpi/metrics/tokens_used")
    assert resp.status_code == 200
    data = resp.json()
    assert "metric_name" in data
    assert "data" in data


@pytest.mark.asyncio
async def test_kpi_report(client):
    """GET /api/v1/kpi/report returns 200 with report structure."""
    resp = await client.get("/api/v1/kpi/report")
    assert resp.status_code == 200
    data = resp.json()
    assert "period" in data
    assert "summary" in data
    assert "generated_at" in data


@pytest.mark.asyncio
async def test_kpi_compare(client):
    """GET /api/v1/kpi/compare with metric_name returns 200."""
    resp = await client.get("/api/v1/kpi/compare", params={"metric_name": "tokens_used"})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_kpi_compare_with_roles(client):
    """GET /api/v1/kpi/compare with metric_name and roles returns 200."""
    resp = await client.get(
        "/api/v1/kpi/compare",
        params={"metric_name": "tokens_used", "roles": ["coding", "test"]},
    )
    assert resp.status_code == 200
