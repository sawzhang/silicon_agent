# Feature-007 大模型快速探活能力出口 - 接口与数据结构

## 1. 后端接口契约（对齐 platform feature-008）

### 1.1 快速探活
```http
GET /api/v1/llm/probe?model=gpt-5.3-codex&timeout_ms=3000
```

Query 参数：
- `model?: string`
- `timeout_ms?: number`（后端约束：`500..10000`，默认 `3000`）

## 2. 前端类型签名（新增）
文件：`src/types/llmProbe.ts`

```ts
export interface LLMProbeRequest {
  model?: string;
  timeout_ms?: number;
}

export interface LLMProbeResponse {
  ok: boolean;
  provider: string;
  base_url: string;
  requested_model?: string | null;
  resolved_model?: string | null;
  latency_ms: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  error_code?: string | null;
  error_message?: string | null;
  checked_at: string;
}
```

## 3. Service 签名（新增）
文件：`src/services/llmProbeApi.ts`

```ts
export async function probeLLM(params?: LLMProbeRequest): Promise<LLMProbeResponse>
```

实现约束：
- 通过 `GET /llm/probe` 调用。
- 将 `params` 作为 query 参数透传。

## 4. Hook 签名（新增）
文件：`src/hooks/useLLMProbe.ts`

```ts
export function useLLMProbe()
```

建议语义：
- 使用 `useMutation` 驱动手动触发探活。
- 不写入全局缓存。

## 5. 页面交互接口
文件：`src/pages/Dashboard/index.tsx`

```ts
const handleProbe = async (payload?: LLMProbeRequest) => Promise<void>
```

- 触发点：系统状态卡中的“模型快速探活”按钮。
- 结果展示：
  - `ok=true`：成功态信息卡。
  - `ok=false`：失败态信息卡（展示 `error_code/error_message`）。

## 6. Mock 数据

### 6.1 成功响应
```json
{
  "ok": true,
  "provider": "openai-compatible",
  "base_url": "https://xxx/v1",
  "requested_model": "gpt-5.3-codex",
  "resolved_model": "gpt-5.3-codex",
  "latency_ms": 428,
  "input_tokens": 8,
  "output_tokens": 3,
  "total_tokens": 11,
  "error_code": null,
  "error_message": null,
  "checked_at": "2026-03-02T10:00:00Z"
}
```

### 6.2 失败响应（业务失败但 HTTP 200）
```json
{
  "ok": false,
  "provider": "openai-compatible",
  "base_url": "https://xxx/v1",
  "requested_model": "gpt-5.3-codex",
  "resolved_model": null,
  "latency_ms": 3002,
  "input_tokens": 0,
  "output_tokens": 0,
  "total_tokens": 0,
  "error_code": "UPSTREAM_TIMEOUT",
  "error_message": "LLM probe timeout",
  "checked_at": "2026-03-02T10:00:00Z"
}
```

### 6.3 参数校验失败（HTTP 422）
```json
{
  "detail": [
    {
      "loc": ["query", "timeout_ms"],
      "msg": "Input should be greater than or equal to 500",
      "type": "greater_than_equal"
    }
  ]
}
```
