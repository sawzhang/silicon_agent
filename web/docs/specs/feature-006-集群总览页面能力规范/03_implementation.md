# Feature-006 集群总览页面能力规范 - 实现细节

## 1. 页面结构
文件：`src/pages/Dashboard/index.tsx`

1. 顶部 KPI 卡片区（4 卡片）。
2. 中部 Agent 集群卡片区（按 `AGENT_ROLES` 固定角色渲染）。
3. 底部两栏：
   - 左：近期活动（`ActivityFeed`）
   - 右：系统状态（活跃 Agent 数 + 成功率）

## 2. 数据加载流程
1. KPI：`useKPISummary()` 获取统计数据。
2. 待审批：`useGateList({ status: 'pending' })` 获取 pending 数。
3. Agent：`useAgentList()` 拉取后，通过 `useEffect` 同步写入 `agentStore`。
4. 活动流：`ActivityFeed` 中读取 `activityStore` + `listAuditLogs` 历史合并。

## 3. 实时更新链路
1. `App.tsx` 全局调用 `useWebSocket()`。
2. WebSocket 事件影响 Dashboard 的路径：
   - `agent_status` -> `agentStore.updateAgent`
   - `activity` -> `activityStore.addActivity`
   - `task_update` -> 失效 `kpi-summary/tasks/cockpit` 查询
   - `gate_created/gate_resolved` -> 通知与刷新版本递增

## 4. 展示规则
1. KPI 卡片：
   - 成本显示 `¥` 前缀，保留 2 位小数。
   - 待审批卡片使用 `Badge` 展示数量并可跳转。
2. Agent 卡片：
   - 使用 `ROLE_DISPLAY_NAMES` 映射中文角色名。
   - 状态映射到 `Badge/Tag` 颜色。
   - 当前任务存在时显示短 task id。
3. 活动流：
   - 优先展示 WS 活动，补充审计日志去重后最多 20 条。
4. 系统状态：
   - 活跃 Agent = `status in ['running','idle']`。
   - 成功率仅在 `success_rate > 0` 时展示。

## 5. 已知边界
1. Dashboard 当前无筛选器（时间/项目/角色维度）。
2. 活动细节展示为字符串，不做结构化展开。
3. 对 WebSocket 连接状态本页无显式 UI 提示。

## 6. 回归检查清单
1. 页面初次加载 KPI/Agent/活动正常展示。
2. Gate pending 数与 `/gates` 跳转可用。
3. Agent 状态在 WS 推送后更新。
4. 活动流可显示 WS + 审计融合结果。
5. 成功率为 0 时右侧不展示成功率卡项。
