# feature-007-集群总览页面能力梳理

## 1. 背景与目标
按“新需求规格”格式梳理当前集群总览（KPI Cockpit）已具备能力，形成可评审、可回归、可扩展的基线文档。

## 2. 范围定义（现状映射）
本项目中“集群总览”对应后端监控看板能力，核心接口为 `GET /api/v1/kpi/cockpit`，并联动 KPI 汇总、ROI、对比与报表能力。

## 3. 用户故事
1. 作为值班/管理员，我需要看到待审批 gate 数、运行中任务数、今日失败数、今日完成数。
2. 作为研发负责人，我需要查看运行任务当前阶段与失败任务错误信息，快速定位异常。
3. 作为业务负责人，我需要查看 ROI 与时间节省评估，用于评估平台收益。
4. 作为分析人员，我需要导出 KPI 报表（JSON/CSV）和按角色对比指标。

## 4. 功能范围（当前已实现）
1. 集群总览主看板：`GET /api/v1/kpi/cockpit`
2. KPI 汇总：`GET /api/v1/kpi/summary`
3. 指标时序：`GET /api/v1/kpi/metrics/{name}`
4. KPI 报表导出：`GET /api/v1/kpi/report?period=daily&format=json|csv`
5. ROI 总览：`GET /api/v1/kpi/roi?days=1..365`
6. 角色指标对比：`GET /api/v1/kpi/compare?metric_name=...&roles=...`
7. 任务联动操作（页面常见）：失败任务重试 `POST /api/v1/tasks/{task_id}/retry`

## 5. 验收标准（现状基线）
1. `cockpit` 返回四个核心计数与四组列表：`pending_gates/running_tasks/failed_tasks/recent_completed`。
2. 运行任务可返回 `current_stage`，失败任务可返回 `error_message`。
3. `roi` 参数 `days` 超范围（如 0、999）返回 422。
4. `report` 支持 `format=csv` 下载，包含 summary 与 by-agent 片段。
5. `compare` 在给定 `metric_name` 时返回各角色最近指标序列。
6. 重试失败任务后，任务状态可回到 `pending` 且失败 stage 被重置。

## 6. 文件路径
### 6.1 已实现代码
- `app/api/v1/kpi.py`
- `app/schemas/kpi.py`
- `app/services/kpi_service.py`
- `app/models/kpi.py`
- `app/api/v1/tasks.py`
- `app/services/task_service.py`

### 6.2 测试证据
- `tests/test_kpi_api.py`
- `tests/test_roi_cockpit.py`
- `tests/test_kpi_service.py`

### 6.3 本次文档新增
- `docs/specs/feature-007-集群总览页面能力梳理/01_requirements.md`
- `docs/specs/feature-007-集群总览页面能力梳理/02_interface.md`
- `docs/specs/feature-007-集群总览页面能力梳理/03_implementation.md`

## 7. 非目标
1. 本文不新增实时推送协议，仅描述当前查询型接口能力。
2. 本文不新增多租户隔离/权限模型。
3. 本文不改动 KPI 计算口径和 token 单价策略。
