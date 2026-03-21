---
name: github_issue_dispatch
display_name: GitHub Issue Dispatch
description: Understands the Github Issue and dispatches tasks to execution agents.
layer: L1
tags: ["github", "dispatch", "orchestrator"]
status: active
version: 1.0.0
---

# GitHub Issue Dispatch Skill

该技能负责分析上游传入的 GitHub Issue 数据，并将其结构化分发给对应的处理 Agent。你需要使用此技能完成以下两项任务：

## 1. 创建任务 (Task Definition)
- **理解 Issue**：阅读并理解收到的 Issue 信息。
- **关联本地项目**：通过识别 Issue 的 URL（如 `starbucks-asg-api`），明确关联到本地记录的项目（例如我们负责的项目 ID 或代号）。
- **提取关键信息**：根据 GitHub Issue 模版的要求，从 Issue 的 Title 和 Content 中提取需要在这个任务中执行的具体关键信息（如需要加密哪些字段）。

## 2. 任务分发 (Task Dispatch)
- **识别执行 Agent**：根据理解出的 Issue 类型和需要进行的修复内容（如加密修改），识别出具体执行该任务的 agent 是谁（例：`安全加密agent`）。
- **打包分发指令**：在你思考和整理后，明确输出分发结语，将第一步归纳出的任务详细数据要求分发给该处理 Issue 的 Agent。
