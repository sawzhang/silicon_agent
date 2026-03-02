# Feature-006 集群总览页面能力规范

## 1. 文档定位
- 类型：能力基线文档（按新需求模板编排）
- 页面：`/dashboard`（集群总览）
- 目标：沉淀当前集群总览已实现能力，作为后续迭代需求输入

## 2. 背景与目标
集群总览页用于展示平台运行健康度与核心指标，当前能力目标：
1. 在单页展示 KPI 核心统计。
2. 展示 Agent 集群实时状态。
3. 展示近期活动与系统运行摘要。
4. 透出待审批数量并可跳转审批中心。

## 3. 用户故事
1. 作为平台运营人员，我希望查看任务规模、Token、成本等 KPI 以快速判断系统负载。
2. 作为值班工程师，我希望查看每个 Agent 当前状态、模型与任务占用信息。
3. 作为审批人员，我希望直接看到待处理审批数量并跳转处理。
4. 作为观察者，我希望看到近期活动时间线，了解系统最近发生的关键行为。

## 4. 功能范围（当前）
1. KPI 总览卡片
   - 任务总数/已完成。
   - Tokens 消耗。
   - 总成本（RMB）。
   - 待处理审批数量（可跳转 `/gates`）。
2. Agent 集群卡片
   - 固定角色集合展示（`AGENT_ROLES`）。
   - 每个角色展示状态、模型、当前任务。
3. 近期活动
   - 合并 WebSocket 实时活动与审计日志历史，最多展示 20 条。
4. 系统状态
   - 展示活跃 Agents 数量（running/idle）。
   - 展示成功率（`success_rate > 0` 时）。

## 5. 非目标
- 不提供 Dashboard 维度筛选（时间范围/项目/角色过滤）。
- 不提供图表级钻取和导出。
- 不提供 Agent 启停控制（该能力在配置页面）。

## 6. 验收标准
1. 页面加载后可展示 KPI 卡片与 Agent 集群卡片。
2. 待审批卡片点击可跳转至 `/gates`。
3. Agent 状态可随 REST + WebSocket 变更而更新。
4. 近期活动可展示实时消息与历史审计融合结果。
5. 系统状态可正确计算活跃 Agent 数与成功率展示条件。

## 7. 文件路径
### 7.1 现有实现文件
- `src/pages/Dashboard/index.tsx`
- `src/components/AgentCard.tsx`
- `src/components/ActivityFeed.tsx`
- `src/hooks/useKPI.ts`
- `src/hooks/useAgents.ts`
- `src/hooks/useGates.ts`
- `src/hooks/useWebSocket.ts`
- `src/services/kpiApi.ts`
- `src/services/agentApi.ts`
- `src/services/gateApi.ts`
- `src/services/auditApi.ts`
- `src/stores/agentStore.ts`
- `src/stores/activityStore.ts`
- `src/types/kpi.ts`
- `src/types/agent.ts`
- `src/types/gate.ts`
- `src/types/websocket.ts`

### 7.2 本次文档新增
- `docs/specs/feature-006-集群总览页面能力规范/01_requirements.md`
- `docs/specs/feature-006-集群总览页面能力规范/02_interface.md`
- `docs/specs/feature-006-集群总览页面能力规范/03_implementation.md`

## 8. 签名约束
详见 `02_interface.md`。

## 9. Mock 数据
详见 `02_interface.md`。
