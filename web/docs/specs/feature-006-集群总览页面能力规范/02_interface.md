# Feature-006 集群总览页面能力规范 - 接口与数据结构

## 1. 关键类型（当前）

### 1.1 KPI
文件：`src/types/kpi.ts`

```ts
export interface KPISummary {
  total_tasks: number;
  completed_tasks: number;
  success_rate: number;
  avg_duration_minutes: number;
  total_tokens: number;
  total_cost_rmb: number;
  metrics: KPIMetricValue[];
}
```

### 1.2 Agent
文件：`src/types/agent.ts`

```ts
export interface AgentStatus {
  id: string;
  role: string;
  display_name: string;
  status: string;
  model_name: string | null;
  current_task_id: string | null;
  started_at: string | null;
  last_active_at: string | null;
}

export interface AgentListResponse {
  agents: AgentStatus[];
}
```

### 1.3 Gate
文件：`src/types/gate.ts`

```ts
export interface Gate {
  id: string;
  gate_type: string;
  task_id: string;
  agent_role: string;
  status: 'pending' | 'approved' | 'rejected' | 'revised';
  created_at: string;
}
```

## 2. API 签名（当前）

### 2.1 KPI / Cockpit
文件：`src/services/kpiApi.ts`

```ts
export async function getKPISummary(period?: string): Promise<KPISummary>
export async function getCockpit(): Promise<CockpitData>
```

### 2.2 Agents
文件：`src/services/agentApi.ts`

```ts
export async function listAgents(): Promise<AgentListResponse>
```

### 2.3 Gates
文件：`src/services/gateApi.ts`

```ts
export async function listGates(params?: { status?: string; task_id?: string }): Promise<Gate[]>
```

### 2.4 Audit Logs
文件：`src/services/auditApi.ts`

```ts
export async function listAuditLogs(params?: {
  role?: string;
  risk_level?: string;
  start_date?: string;
  end_date?: string;
  page?: number;
  page_size?: number;
}): Promise<AuditListResponse>
```

## 3. Hook 签名（当前）

```ts
// src/hooks/useKPI.ts
export function useKPISummary(period?: string)

// src/hooks/useAgents.ts
export function useAgentList()

// src/hooks/useGates.ts
export function useGateList(params?: { status?: string; task_id?: string })
```

## 4. 页面交互接口（当前）
文件：`src/pages/Dashboard/index.tsx`

```ts
const Dashboard: React.FC
```

数据来源：
- KPI：`useKPISummary()`
- 待审批：`useGateList({ status: 'pending' })`
- Agent 列表：`useAgentList()` + `useAgentStore.updateAgent()`
- 活动流：`ActivityFeed` 内部 `listAuditLogs(...)` + `useActivityStore.activities`

## 5. Mock 数据

### 5.1 KPI 汇总 `GET /kpi/summary`
```json
{
  "total_tasks": 120,
  "completed_tasks": 96,
  "success_rate": 92.5,
  "avg_duration_minutes": 18.3,
  "total_tokens": 1823400,
  "total_cost_rmb": 512.67,
  "metrics": []
}
```

### 5.2 Agent 列表 `GET /agents`
```json
{
  "agents": [
    {
      "id": "ag_001",
      "role": "coding",
      "display_name": "研发官",
      "status": "running",
      "model_name": "gpt-5-codex",
      "current_task_id": "task_123",
      "started_at": "2026-03-02T08:10:00Z",
      "last_active_at": "2026-03-02T08:12:00Z"
    }
  ]
}
```

### 5.3 待审批 `GET /gates?status=pending`
```json
{
  "items": [
    {
      "id": "gate_001",
      "gate_type": "plan_review",
      "task_id": "task_123",
      "agent_role": "spec",
      "status": "pending",
      "created_at": "2026-03-02T08:00:00Z"
    }
  ],
  "total": 1
}
```

### 5.4 活动日志 `GET /audit/logs?page=1&page_size=20`
```json
{
  "items": [
    {
      "id": "aud_001",
      "agent_role": "coding",
      "action_type": "tool_call_executed",
      "action_detail": { "command": "npm test" },
      "risk_level": "low",
      "created_at": "2026-03-02T08:11:00Z"
    }
  ],
  "total": 1,
  "page": 1,
  "page_size": 20
}
```
