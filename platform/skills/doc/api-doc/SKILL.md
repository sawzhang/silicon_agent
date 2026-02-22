---
name: api-doc
description: 根据代码自动生成 API 文档，包括接口说明、参数和示例
metadata:
  emoji: "📖"
  tags: ["doc", "api", "documentation"]
---

# API 文档生成

根据代码和接口定义自动生成 API 文档，包括接口描述、参数说明、请求/响应示例。

## 输入

用户会提供：
- `target`：要生成文档的模块或 API 路由文件
- `format`（可选）：文档格式（默认 Markdown）

## 工作流

### Step 1：收集 API 定义

使用 `search-codebase` skill 和 `read` 工具获取：
- 路由定义文件
- 请求/响应 Schema（Pydantic model 等）
- 中间件和认证配置
- 已有的注释和 docstring

### Step 2：提取接口信息

从代码中提取每个接口的：
- HTTP 方法和路径
- 路径参数、查询参数、请求体
- 响应格式和状态码
- 认证要求
- 限流规则（如有）

### Step 3：生成请求示例

为每个接口生成：
- curl 命令示例
- 请求体 JSON 示例（使用合理的示例数据）
- 成功响应示例
- 常见错误响应示例

### Step 4：组织文档结构

按功能模块组织接口文档，确保：
- 目录清晰
- 接口按逻辑分组
- 通用说明（认证、错误格式）在文档开头

## 输出格式

```markdown
## API 文档：{模块名称}

### 概述
{模块功能描述}

### 认证
{认证方式说明}

### 通用错误格式
```json
{
  "error": {"code": "ERROR_CODE", "message": "错误描述"}
}
```

---

### {接口分组名称}

#### {METHOD} {path}
{接口描述}

**参数**：
| 参数 | 位置 | 类型 | 必填 | 说明 |
|------|------|------|------|------|
| {name} | {path/query/body} | {type} | {是/否} | {说明} |

**请求示例**：
```bash
curl -X {METHOD} {url} -H "Content-Type: application/json" -d '{json}'
```

**成功响应** `{status}`：
```json
{响应示例}
```
```
