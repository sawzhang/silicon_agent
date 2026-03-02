# feature-008-大模型快速探活

## 1. 背景与目标
当前平台仅有服务级 `/health`，无法快速判断“大模型通道是否可用、延迟是否异常、当前模型是否可调用”。新增一个低成本、低时延的模型探活能力，供页面或运维脚本快速诊断。

## 2. 用户故事
1. 作为运维人员，我希望一键检查 LLM 连通性，快速区分“平台故障”与“模型通道故障”。
2. 作为研发人员，我希望探活固定使用平台配置模型，确保结果与生产默认配置一致。
3. 作为值班人员，我希望拿到明确失败原因（超时/鉴权/上游错误），便于快速处置。

## 3. 功能范围（新增）
1. 新增快速探活接口：`GET /api/v1/llm/probe`
2. 支持可选参数：
   - `timeout_ms`：覆盖默认超时（受上下限约束）
3. 探活模型固定使用 `.env` 中的 `LLM_MODEL`
4. 探活方式：调用一次最小化 chat completion（极短 prompt + 极小 max_tokens）
5. 返回内容：可用性、耗时、实际模型、错误分类、时间戳
6. 不写业务表，不触发任务，不影响任务队列

## 4. 验收标准
1. 正常可用时返回 `200`，`ok=true`，并包含 `latency_ms`。
2. 上游异常时返回 `200`，`ok=false`，并包含标准化 `error_code/error_message`。
3. `timeout_ms` 超出范围返回 `422`。
4. 探活请求不产生任务记录和 stage 日志。
5. 接口单次探活应为“低成本调用”（max_tokens 固定小值）。

## 5. 文件路径
### 5.1 需修改/新增代码
- `app/api/v1/router.py`（挂载新路由）
- `app/api/v1/llm_probe.py`（新增）
- `app/schemas/llm_probe.py`（新增）
- `app/services/llm_probe_service.py`（新增）
- `app/integration/llm_client.py`（可选：抽公共探活调用）

### 5.2 测试文件
- `tests/test_llm_probe_api.py`（新增）
- `tests/test_llm_probe_service.py`（新增）

### 5.3 本次文档新增
- `docs/specs/feature-008-大模型快速探活/01_requirements.md`
- `docs/specs/feature-008-大模型快速探活/02_interface.md`
- `docs/specs/feature-008-大模型快速探活/03_implementation.md`

## 6. 非目标
1. 不做持续健康监控任务（cron）与告警系统接入。
2. 不做模型基准评测（质量评估、长链路压测）。
3. 不修改任务执行引擎逻辑。
