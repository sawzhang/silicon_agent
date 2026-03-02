# Feature-007 大模型快速探活能力出口 - 实现细节

## 1. 实现步骤
1. 新增 `llmProbe` 类型定义（请求/响应）。
2. 新增 `llmProbeApi` service，封装 `GET /llm/probe`。
3. 新增 `useLLMProbe` hook（mutation）。
4. 在 Dashboard“系统状态”卡添加“模型快速探活”按钮。
5. 增加探活结果展示区域（Modal/内嵌卡片）。

## 2. 页面交互设计

### 2.1 触发流程
1. 用户点击“模型快速探活”。
2. 可选填写 `model` 与 `timeout_ms`（默认 3000）。
3. 提交后按钮进入 loading。
4. 请求完成后展示结果详情。

### 2.2 展示规则
- `ok=true`：
  - 显示“可用”状态。
  - 显示 `latency_ms`、`resolved_model`、`checked_at`。
- `ok=false`：
  - 显示“不可用”状态。
  - 显示 `error_code`、`error_message`、`latency_ms`。

### 2.3 错误处理
- 422：提示“timeout_ms 越界”。
- 网络异常：提示“探活请求失败，请重试”。
- 其余错误：透传后端 `detail`。

## 3. 状态管理
1. 探活结果状态仅保存在 Dashboard 页面本地状态中。
2. 探活结果不写入全局 store、不参与 WebSocket。
3. 连续探活以最新结果覆盖旧结果。

## 4. 回归检查清单
1. Dashboard 原有 KPI / Agent / 活动展示不受影响。
2. 探活成功时结果展示正确。
3. 探活失败（`ok=false`）时错误信息展示正确。
4. 422 与网络异常提示可见。
5. 多次点击探活时按钮 loading 与结果覆盖正常。
