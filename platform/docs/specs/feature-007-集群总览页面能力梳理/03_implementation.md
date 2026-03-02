# feature-007-集群总览页面能力梳理 - 实现细节

## 1. 页面能力流程（现状）
1. 页面初始化请求 `GET /api/v1/kpi/cockpit`，渲染总览卡片与任务/gate 列表。
2. 可并行请求 `GET /api/v1/kpi/summary` 获取全局任务成功率、token、成本汇总。
3. ROI 面板调用 `GET /api/v1/kpi/roi?days=N` 展示成本、节省、时间收益与角色效率。
4. 指标图表调用 `GET /api/v1/kpi/metrics/{name}`，按 `agent_role` 可选过滤。
5. 对比视图调用 `GET /api/v1/kpi/compare` 返回角色维度最近指标序列。
6. 导出能力调用 `GET /api/v1/kpi/report?format=csv` 获取可下载报表。
7. 对失败任务可联动 `POST /api/v1/tasks/{id}/retry` 做手动恢复。

## 2. 后端实现要点

### 2.1 Cockpit 统计口径
- `pending_gates`：`HumanGateModel.status == "pending"`
- `running_tasks`：`TaskModel.status == "running"`
- `failed_tasks_today`：`TaskModel.status == "failed" and completed_at >= 今日0点`
- `completed_tasks_today`：`TaskModel.status == "completed" and completed_at >= 今日0点`
- `recent_completed`：最近 10 条完成任务

### 2.2 任务条目构造
- 从 `task.stages` 推导 `current_stage`（首个 running stage）
- 从失败 stage 提取 `error_message`
- 输出 tokens/cost 便于总览直接展示

### 2.3 ROI 计算逻辑
- 仅统计时间窗内已完成任务（`completed_at >= now - days`）
- 人工成本估算：
  - 优先 `template.estimated_hours`
  - 否则使用全局 `ESTIMATED_HOURS_PER_TASK`
  - 再乘 `HOURLY_RATE_RMB`
- 角色成本估算：`tokens / 1000 * CB_TOKEN_PRICE_PER_1K`

### 2.4 报表与对比
- `report` 汇总 `summary + by_agent`，支持 JSON/CSV 双格式。
- `compare` 返回目标指标在多个角色上的最近 10 条值，适合趋势对照。

## 3. 已知边界与限制
1. 当前总览能力主要为查询接口，不提供独立 WebSocket 总览事件。
2. `KPIReportResponse.by_agent` 使用 `KPISummaryResponse` 结构，`total_tasks` 实际承载 stage 数。
3. `compare` 默认角色集合固定（orchestrator/spec/coding/test/review/smoke/doc）。
4. ROI 是估算口径，受模板工时和全局费率配置影响。

## 4. 测试覆盖证据
- `tests/test_kpi_api.py`
  - summary/metrics/report/compare 基础可用性
- `tests/test_roi_cockpit.py`
  - roi 参数校验、收益字段、cockpit 列表结构、失败任务重试联动
- `tests/test_kpi_service.py`
  - `get_roi_summary` 与 `get_cockpit` 细粒度单元覆盖

## 5. 后续扩展建议（文档层）
1. 若要支持“集群实时总览”，建议补充总览维度 WS 事件协议与订阅方式。
2. 若要降低查询开销，建议为 cockpit 计数与列表引入聚合缓存层。
3. 若要做跨项目/租户对比，需在本 Spec 增加权限与隔离约束说明。
