# Feature-007 大模型快速探活能力出口

## 1. 文档定位
- 类型：新增需求 Spec（To-Be）
- 范围：前端“集群总览”页面增加大模型快速探活入口
- 对齐后端：`platform/docs/specs/feature-008-大模型快速探活`

## 2. 背景与目标
后端已提供 `GET /api/v1/llm/probe`，但前端没有直接探活入口。当前排障需要切到后端工具或手工请求，成本高。

目标：
1. 在页面提供“一键探活”能力出口。
2. 展示探活结果（可用性、延迟、模型、错误原因）。
3. 支持可选探活参数（`model`、`timeout_ms`）用于快速诊断模型切换与超时问题。

## 3. 用户故事
1. 作为值班工程师，我希望在 Dashboard 直接点击探活，快速判断模型通道是否可用。
2. 作为研发人员，我希望指定模型进行探活，验证模型切换是否生效。
3. 作为运维人员，我希望看到标准错误码与错误信息，便于快速定位（超时/鉴权/上游错误）。

## 4. 功能范围
1. 集群总览页面（`/dashboard`）
   - 在“系统状态”卡片新增“模型快速探活”按钮。
   - 点击后发起 `GET /api/v1/llm/probe`。
   - 支持参数：`model?`、`timeout_ms?`（默认 3000）。
   - 展示结果：`ok`、`latency_ms`、`requested_model`、`resolved_model`、`error_code`、`error_message`、`checked_at`。
2. 结果展示形式
   - 建议使用 `Modal` 或内嵌面板。
   - 成功与失败均视为有效探测结果（HTTP 200，依据 `ok` 判断）。
3. 错误处理
   - 参数越界（422）或网络异常时提示并保留可重试入口。

## 5. 非目标
- 不实现定时探活任务。
- 不做历史探活记录持久化。
- 不接入告警系统。

## 6. 验收标准
1. Dashboard 可触发探活请求并展示结果。
2. `ok=true` 时显示延迟与模型信息。
3. `ok=false` 时显示标准化错误码与错误信息。
4. 支持可选 `model/timeout_ms` 参数，并在 `timeout_ms` 越界时给出可见错误提示。
5. 探活不影响现有任务/审批/活动流功能。

## 7. 文件路径
### 7.1 需修改/新增
- `src/pages/Dashboard/index.tsx`
- `src/services/llmProbeApi.ts`（新增）
- `src/hooks/useLLMProbe.ts`（新增）
- `src/types/llmProbe.ts`（新增）

### 7.2 关联文件（只读）
- `src/services/api.ts`
- `src/utils/formatters.ts`

## 8. 签名约束
详见 `02_interface.md`。

## 9. Mock 数据
详见 `02_interface.md`。
