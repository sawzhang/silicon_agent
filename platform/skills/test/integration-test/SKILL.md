---
name: integration-test
description: 生成集成测试，验证多个模块之间的协作是否正确
metadata:
  emoji: "🔗"
  tags: ["test", "integration-test", "testing"]
---

# 集成测试生成

生成集成测试，验证多个模块、服务或层之间的协作是否正确。

## 输入

用户会提供：
- `target`：要测试的功能或模块组合
- `api_spec`（可选）：API 接口规范
- `test_framework`（可选）：测试框架（默认 pytest）

## 工作流

### Step 1：分析集成点

使用 `search-codebase` skill 和 `read` 工具分析：
- 模块间的调用关系
- 数据流经的组件
- 外部依赖（数据库、消息队列、第三方 API 等）
- 需要真实执行 vs 需要 mock 的部分

### Step 2：设计测试场景

为关键集成路径设计测试：
- **端到端流程**：完整的业务流程（如创建→查询→更新→删除）
- **跨层调用**：API → Service → Repository 的完整链路
- **错误传播**：底层错误是否正确传递到上层
- **数据一致性**：操作后数据库状态是否正确

### Step 3：准备测试环境

确定测试环境配置：
- 数据库 fixture（使用测试数据库或内存数据库）
- 测试数据准备（factory 或 fixture）
- 外部服务 mock（HTTP mock、消息队列 mock 等）

### Step 4：生成测试代码

按场景生成集成测试代码：
- 使用 `pytest` + `httpx.AsyncClient`（API 测试）
- 使用数据库事务回滚保证测试隔离
- 测试命名：`test_integration_{feature}_{scenario}`
- 清晰的 setup / teardown 逻辑

### Step 5：质量检查

确保：
- 测试覆盖了核心业务流程
- 测试之间数据隔离
- 不依赖外部服务的可用性
- 执行时间合理

## 输出格式

使用 `write` 工具将测试代码写入工作目录，同时输出摘要：

```markdown
## 集成测试报告

### 测试目标
{功能/模块组合}

### 测试场景
| # | 场景 | 覆盖模块 | 类型 |
|---|------|---------|------|
| 1 | {场景描述} | A → B → C | 端到端 |
| 2 | {场景描述} | A → B | 错误传播 |

### 环境依赖
- 数据库：{配置}
- Mock 服务：{列表}

### 生成文件
- `{test_file_path}`
```
