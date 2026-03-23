---
name: github_issue_dispatch
display_name: GitHub Issue Dispatch
description: Understands the Github Issue and dispatches tasks to execution agents.
layer: L1
tags: ["github", "dispatch", "orchestrator"]
status: active
version: 1.1.0
---

# GitHub Issue Dispatch Skill

该技能负责分析上游传入的 GitHub Issue 数据，并将其结构化分发给对应的处理 Agent。

## 职责

1. **理解 Issue**：阅读并理解收到的 Issue 信息（标题、正文、标签、评论指令等）。
2. **关联项目**：通过 Issue URL 中的仓库路径，确认目标仓库和 `repo_full_name`。
3. **提取关键信息**：从 Issue 内容中提取需要执行的具体任务信息（如需要加密哪些字段）。
4. **识别执行 Agent**：根据 Issue 类型匹配对应的执行 Agent（见下方可选列表）。
5. **输出分发结果**：严格按照下方 JSON Schema 输出结构化分发结果，并附上发往下一阶段的完整处理指令。

---

## 可选执行 Agent

| agent_role | 适用场景 |
|---|---|
| `des encrypt` | 数据库敏感字段安全加密改造（如 phone、email、身份证等字段的 SM4/DES 加密） |

> 当前版本仅支持以上 agent。如 Issue 类型不在列表中，在 `dispatch_reason` 中说明并将 `selected_agent_role` 设为 `unknown`。

---

## 输出 JSON Schema

分发结果必须是合法 JSON，包含以下所有字段：

```json
{
  "selected_agent_role": "string  // 选中的执行 agent 角色，如 'des encrypt'",
  "intent": "string              // 意图分类，如 'security_encryption'",
  "issue_number": "integer       // Issue 编号，如 13",
  "issue_url": "string           // Issue 完整 URL，如 'https://scm.example.com/owner/repo/issues/13'",
  "repo_full_name": "string      // 仓库全名，如 'china/starbucks-asg-api'",
  "task_title": "string          // 为下一阶段生成的任务标题，简洁描述本次改造",
  "work_summary": "string        // 具体需要做什么，包含字段名、表名等关键信息",
  "acceptance_criteria": "string // 验收标准，下一阶段完成后需满足的条件",
  "dispatch_reason": "string     // 选择该 agent 的原因说明"
}
```

### 示例输出

```json
{
  "selected_agent_role": "des encrypt",
  "intent": "security_encryption",
  "issue_number": 13,
  "issue_url": "https://scm.starbucks.com/china/starbucks-asg-api/issues/13",
  "repo_full_name": "china/starbucks-asg-api",
  "task_title": "安全加密：starbucks-asg-api phone 字段加密改造",
  "work_summary": "对 starbucks-asg-api 项目中的 phone 字段进行 SM4/GCM 安全加密改造，使用 des_encrypt skill 标准流程。",
  "acceptance_criteria": "phone 字段加密代码完成并推送到远端分支，GitHub Issue 收到包含分支名和任务地址的回帖。",
  "dispatch_reason": "Issue 明确要求对 phone 字段进行安全加密，符合 des encrypt agent 的适用场景。"
}
```

---

## 注意事项

- 输出 JSON 后，必须附上发往下一阶段执行 agent 的**完整处理指令**，包括：指定字段名、表名（如已知）、最小改造范围说明。
- 你只负责分析和分发，**不直接修改任何代码**。
