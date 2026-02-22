---
name: design-api
description: 设计 RESTful API 接口，包括路由、请求/响应格式和错误码定义
metadata:
  emoji: "🌐"
  tags: ["spec", "api", "design"]
---

# REST API 接口设计

根据需求设计 RESTful API 接口规范，包括路由定义、请求/响应格式、认证方式和错误处理。

## 输入

用户会提供：
- `feature`：功能描述
- `entities`：涉及的数据实体
- `constraints`（可选）：技术约束（如框架、认证方式等）

## 工作流

### Step 1：资源识别

从功能描述中识别 REST 资源：
- 核心资源（名词）及其层级关系
- 资源间的关联关系（1:1, 1:N, M:N）

### Step 2：路由设计

为每个资源设计 CRUD 路由，遵循 RESTful 规范：
- 使用复数名词作为路由
- 合理使用嵌套路由（不超过 2 层）
- 对非 CRUD 操作使用动作子资源（如 `/orders/{id}/cancel`）

### Step 3：请求/响应格式

为每个接口定义：
- HTTP 方法和路径
- 请求参数（path / query / body）
- 响应格式（成功和失败）
- 状态码

### Step 4：搜索现有代码

使用 `search-codebase` skill 查找项目中已有的 API 模式，确保新接口与现有风格一致。

## 输出格式

```markdown
## API 设计文档：{feature}

### 资源总览
| 资源 | 路径 | 说明 |
|------|------|------|
| {Resource} | /api/v1/{resources} | {说明} |

### 接口详情

#### {METHOD} /api/v1/{path}
- **描述**：{说明}
- **认证**：{是否需要 / 认证方式}
- **请求参数**：
  ```json
  {请求体示例}
  ```
- **成功响应** `{status_code}`：
  ```json
  {响应体示例}
  ```
- **错误响应**：
  | 状态码 | 错误码 | 说明 |
  |--------|--------|------|
  | 400 | INVALID_PARAM | 参数校验失败 |
  | 404 | NOT_FOUND | 资源不存在 |
```
