# feature-008-大模型快速探活 - 实现细节

## 1. 流程设计
1. API 层接收 `timeout_ms` 参数并做边界校验。
2. Service 层开始计时，构造最小化 probe 消息（如 `"ping"`）。
3. 调用 `LLMClient.chat(...)`，参数固定低成本：
   - `temperature=0`
   - `max_tokens=8`（或同级别小值）
   - `model=settings.LLM_MODEL`
4. 成功：返回 `ok=true` 与 token 用量、耗时、模型名。
5. 异常：捕获并映射为标准错误码，返回 `ok=false`（HTTP 200）。

## 2. 关键实现点

### 2.1 低成本探活约束
- 禁止复用业务 prompt。
- 固定短 prompt 和小 `max_tokens`。
- 探活结果只用于诊断，不进入任务流水。

### 2.2 超时策略
- API 参数 `timeout_ms` 仅作用于本次探活。
- 通过短生命周期 `httpx.AsyncClient(timeout=...)` 或临时 client 实现，不污染全局客户端默认超时。

### 2.3 错误映射策略
- `httpx.TimeoutException` -> `UPSTREAM_TIMEOUT`
- `httpx.HTTPStatusError`:
  - 401/403 -> `UPSTREAM_AUTH_ERROR`
  - 400 -> `UPSTREAM_BAD_REQUEST`
  - 5xx -> `UPSTREAM_SERVER_ERROR`
- 其他异常 -> `PROBE_INTERNAL_ERROR`

### 2.4 安全与日志
- 返回值中不包含 API Key。
- `base_url` 仅用于定位环境。
- 记录简要日志（成功/失败、耗时、错误码），不记录完整敏感请求体。

## 3. 依赖注入与路由挂载
1. 在 `app/api/v1/llm_probe.py` 新增路由 `prefix="/llm"`。
2. 在 `app/api/v1/router.py` 挂载 `llm_probe.router`。
3. 在 `app/dependencies.py` 新增 `get_llm_probe_service()`。

## 4. 测试设计

### 4.1 API 测试
- 成功场景：返回 `ok=true` 和 `latency_ms>0`
- 超时场景：返回 `ok=false` + `UPSTREAM_TIMEOUT`
- 参数校验：`timeout_ms` 越界返回 422

### 4.2 Service 单测
- 覆盖错误映射分支（timeout/401/400/5xx/unknown）
- 覆盖固定模型逻辑（`requested_model=settings.LLM_MODEL` 与 `resolved_model`）
- 覆盖 token 字段默认值与时间戳填充

## 5. 回滚与兼容
1. 新增独立路由，不影响现有任务流程。
2. 失败时返回 `ok=false`，避免影响调用方链路稳定性。
3. 如需回滚，仅移除 `/api/v1/llm/probe` 路由与对应 service/schema。
