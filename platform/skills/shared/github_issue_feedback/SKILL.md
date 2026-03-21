---
name: github_issue_feedback
display_name: GitHub Issue Feedback
description: Report the processing result back to the GitHub Issue via curl webhook.
layer: L1
tags: ["github", "feedback"]
status: active
version: 1.0.0
---

# GitHub Issue Feedback Skill

安全加密的 Agent 在按照 `des_encrypt` skill 完成 Coding 并且 Git Commit & Push 分支后，必须使用此技能，通过 REST API 调用来完结 Issue 的反馈流程。

## 职责描述 (Responsibilities)
- 提取当前处理任务的 **Silicon 任务 URL** (`http://127.0.0.1:3000/tasks/<YOUR_TASK_ID>`) 和 **代码所在的远程 Git Branch**（如 `security-fix-13`）。
- 将以上信息以评论回帖的形式返回给 GitHub。

## 技能调用指南
你需要运用宿主内置的基础终端执行工具 (Execute shell commands)，结合上下文中拿到的 `$GHE_TOKEN`，用标准的 CURL 命令提交。

**评论内容格式要求范本：**
```
安全加密编码已完成！
- Git 分支: <你的 Git 分支>
- Silicon Agent 任务地址: <任务 URL>
```

**执行命令参考示例：**
```bash
# 请将 OWNER, REPO, ISSUE_NUMBER, GHE_TOKEN 替换并执行
curl -s -X POST -H "Authorization: token $GHE_TOKEN" \
  -H "Accept: application/vnd.github.v3+json" \
  "https://scm.starbucks.com/api/v3/repos/{OWNER}/{REPO}/issues/{ISSUE_NUMBER}/comments" \
  -d '{"body": "安全加密编码已完成！\n- Git 分支: security-fix-13\n- Silicon Agent 任务地址: http://127.0.0.1:3000/tasks/YOUR_TASK_ID"}'
```
此行为标志着整个 Issue 生命周期的真正交付与闭环。
