---
name: github_issue_feedback
display_name: GitHub Issue Feedback
description: Report the processing result back to the GitHub Issue via curl webhook.
layer: L1
tags: ["github", "feedback"]
status: active
version: 1.1.0
---

# GitHub Issue Feedback Skill

在按照对应 skill 完成 Coding 并且 Git Commit & Push 分支后，必须使用此技能，通过 REST API 将结果回帖到原始 GitHub Issue。

## 职责

1. 从前序阶段（dispatch_issue）的 prior output 中提取以下信息：
   - `issue_number`：Issue 编号（整数）
   - `issue_url`：Issue 完整 URL（用于推断 API base 和 owner/repo）
   - `repo_full_name`：仓库全名，格式 `owner/repo`
2. 从当前 task 上下文中获取：
   - `$GHE_TOKEN` 或 `$GITHUB_TOKEN`：GitHub API 认证 Token
   - `$APP_BASE_URL`：Silicon Agent 前端地址（默认 `http://127.0.0.1:3000`）
   - 当前推送的 Git 分支名（通过 `git branch --show-current` 或 git push 输出获取）
   - 当前 task ID（在任务上下文或环境变量中）

## 推断 API Base URL

从 `issue_url` 中提取 hostname：

- 若 hostname 是 `github.com`，API base 为 `https://api.github.com`
- 若是 GHE 内网域名（如 `scm.example.com`），API base 为 `https://<hostname>/api/v3`
- Token 选择：公网 GitHub 用 `$GITHUB_TOKEN`，GHE 用 `$GHE_TOKEN`

## 评论内容格式

```
任务已完成！
- Git 分支: <你推送的分支名>
- Silicon Agent 任务地址: <$APP_BASE_URL>/tasks/<task_id>
```

## 执行命令参考

```bash
# 公网 GitHub
curl -s -X POST \
  -H "Authorization: token $GITHUB_TOKEN" \
  -H "Accept: application/vnd.github.v3+json" \
  "https://api.github.com/repos/{OWNER}/{REPO}/issues/{ISSUE_NUMBER}/comments" \
  -d '{"body": "任务已完成！\n- Git 分支: {BRANCH}\n- Silicon Agent 任务地址: {APP_BASE_URL}/tasks/{TASK_ID}"}'

# GitHub Enterprise (GHE)
curl -s -X POST \
  -H "Authorization: token $GHE_TOKEN" \
  -H "Accept: application/vnd.github.v3+json" \
  "https://{GHE_HOST}/api/v3/repos/{OWNER}/{REPO}/issues/{ISSUE_NUMBER}/comments" \
  -d '{"body": "任务已完成！\n- Git 分支: {BRANCH}\n- Silicon Agent 任务地址: {APP_BASE_URL}/tasks/{TASK_ID}"}'
```

> 注意：将 `{OWNER}`, `{REPO}`, `{ISSUE_NUMBER}`, `{BRANCH}`, `{APP_BASE_URL}`, `{TASK_ID}`, `{GHE_HOST}` 替换为实际值后执行。

此行为标志着整个 Issue 生命周期的真正交付与闭环。
