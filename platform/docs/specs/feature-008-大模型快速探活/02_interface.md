# feature-008-大模型快速探活 - 接口与数据结构

## 1. 接口定义

### 1.1 快速探活
```http
GET /api/v1/llm/probe?timeout_ms=3000
```

Query 参数：
- `timeout_ms`（可选）：探活超时毫秒，建议范围 `500..10000`，默认 `3000`
- 探活模型固定为 `.env` 配置项 `LLM_MODEL`

## 2. 响应结构

### 2.1 LLMProbeResponse
```json
{
  "ok": true,
  "provider": "openai-compatible",
  "base_url": "https://xxx/v1",
  "requested_model": "LLM_MODEL_FROM_ENV",
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

失败示例：
```json
{
  "ok": false,
  "provider": "openai-compatible",
  "base_url": "https://xxx/v1",
  "requested_model": "LLM_MODEL_FROM_ENV",
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

## 3. 核心签名（拟新增）

### 3.1 API
```python
# app/api/v1/llm_probe.py
@router.get("/probe", response_model=LLMProbeResponse)
async def probe_llm(
    timeout_ms: int = Query(3000, ge=500, le=10000),
    service: LLMProbeService = Depends(get_llm_probe_service),
) -> LLMProbeResponse:
    """Execute a low-cost LLM liveness probe and return normalized diagnostics."""
```

### 3.2 Service
```python
# app/services/llm_probe_service.py
class LLMProbeService:
    async def probe(self, timeout_ms: int = 3000) -> LLMProbeResponse:
        """Run a minimal chat-completions call to verify LLM connectivity and latency."""
```

### 3.3 Schema
```python
# app/schemas/llm_probe.py
class LLMProbeResponse(BaseModel):
    """Normalized response payload for quick LLM liveness checks."""

    ok: bool
    provider: str
    base_url: str
    requested_model: Optional[str] = None
    resolved_model: Optional[str] = None
    latency_ms: int
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    checked_at: datetime
```

## 4. 错误码规范（拟新增）
- `UPSTREAM_TIMEOUT`：上游模型超时
- `UPSTREAM_AUTH_ERROR`：鉴权失败（401/403）
- `UPSTREAM_BAD_REQUEST`：参数错误（400）
- `UPSTREAM_SERVER_ERROR`：上游 5xx
- `PROBE_INTERNAL_ERROR`：平台内部异常
