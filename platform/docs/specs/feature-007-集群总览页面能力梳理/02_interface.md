# feature-007-集群总览页面能力梳理 - 接口与数据结构

## 1. 接口清单

### 1.1 总览与统计
```http
GET /api/v1/kpi/cockpit
GET /api/v1/kpi/summary
GET /api/v1/kpi/roi?days=30
GET /api/v1/kpi/compare?metric_name=tokens_used&roles=coding&roles=test
GET /api/v1/kpi/metrics/{name}?agent_role=coding
GET /api/v1/kpi/report?period=daily&format=json
GET /api/v1/kpi/report?period=daily&format=csv
```

### 1.2 页面联动动作
```http
POST /api/v1/tasks/{task_id}/retry
```

## 2. 核心签名（现状）

### 2.1 KPI API
```python
@router.get("/summary", response_model=KPISummaryResponse)
async def get_kpi_summary(service: KPIService = Depends(get_kpi_service))

@router.get("/metrics/{name}", response_model=KPITimeSeriesResponse)
async def get_kpi_metric(name: str, agent_role: Optional[str] = None, service: KPIService = Depends(get_kpi_service))

@router.get("/report")
async def get_kpi_report(period: str = "daily", format: str = "json", service: KPIService = Depends(get_kpi_service))

@router.get("/roi", response_model=ROISummaryResponse)
async def get_roi_summary(days: int = Query(30, ge=1, le=365), service: KPIService = Depends(get_kpi_service))

@router.get("/cockpit", response_model=CockpitResponse)
async def get_cockpit(service: KPIService = Depends(get_kpi_service))

@router.get("/compare")
async def compare_kpi(metric_name: str, roles: Optional[List[str]] = Query(None), service: KPIService = Depends(get_kpi_service))
```

### 2.2 KPIService
```python
class KPIService:
    async def get_summary(self) -> KPISummaryResponse
    async def get_timeseries(self, metric_name: str, agent_role: Optional[str] = None) -> KPITimeSeriesResponse
    async def generate_report(self, period: str = "daily") -> KPIReportResponse
    async def compare(self, metric_name: str, roles: Optional[List[str]] = None) -> Dict[str, List[KPIMetricValue]]
    async def get_roi_summary(self, days: int = 30) -> ROISummaryResponse
    async def get_cockpit(self) -> CockpitResponse
```

### 2.3 任务重试联动
```python
@router.post("/{task_id}/retry", response_model=TaskDetailResponse)
async def retry_task(task_id: str, service: TaskService = Depends(get_task_service))
```

## 3. 数据结构

### 3.1 CockpitResponse（关键字段）
- 计数：`pending_gates_count`, `running_tasks_count`, `failed_tasks_today`, `completed_tasks_today`
- 列表：`pending_gates`, `running_tasks`, `failed_tasks`, `recent_completed`

### 3.2 CockpitTaskItem（关键字段）
- `id`, `title`, `status`, `project_name`, `template_name`
- `created_at`, `completed_at`
- `current_stage`, `error_message`
- `total_tokens`, `total_cost_rmb`

### 3.3 ROISummaryResponse（关键字段）
- 汇总：`total_tasks_completed`, `total_agent_cost_rmb`, `total_estimated_manual_rmb`, `total_savings_rmb`, `roi_ratio`
- 时间：`total_agent_hours`, `total_estimated_manual_hours`, `time_saved_hours`
- 基准：`benchmark_hours_per_task`, `benchmark_hourly_rate`
- 拆分：`by_role`, `recent_tasks`

## 4. Mock Data

### 4.1 集群总览响应片段
```json
{
  "pending_gates_count": 1,
  "running_tasks_count": 2,
  "failed_tasks_today": 1,
  "completed_tasks_today": 8,
  "running_tasks": [
    {
      "id": "cockpit-task-running",
      "title": "API Refactor",
      "status": "running",
      "current_stage": "coding",
      "total_tokens": 15000,
      "total_cost_rmb": 0.15
    }
  ],
  "failed_tasks": [
    {
      "id": "cockpit-task-failed",
      "title": "Broken Build",
      "status": "failed",
      "error_message": "Test suite failed with 3 errors"
    }
  ]
}
```

### 4.2 ROI 响应片段
```json
{
  "total_tasks_completed": 12,
  "total_agent_cost_rmb": 3.28,
  "total_estimated_manual_rmb": 14400,
  "total_savings_rmb": 14396.72,
  "roi_ratio": 4389.24,
  "benchmark_hours_per_task": 8.0,
  "benchmark_hourly_rate": 150.0,
  "by_role": [],
  "recent_tasks": []
}
```
