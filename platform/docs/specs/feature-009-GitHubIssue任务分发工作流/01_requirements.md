# feature-009-GitHubIssue任务分发工作流

## 1. 背景与目标
为 Silicon Agent 平台补齐 “GitHub issue 触发任务 -> distribution agent 分发 -> worker agent 执行 -> 回帖 issue” 的标准工作流。当前首个真实落地场景是 GHE 仓库 `china/starbucks-asg-api` 的 issue `#13`，需要把 issue 中的安全加密需求分发给 `安全加密agent` 执行。

## 2. 真实样本
2026-03-21 通过 GHE API 获取到：
- 仓库：`china/starbucks-asg-api`
- issue 编号：`13`
- 标题：`安全加密`
- 正文：`安全加密agent，对本项目的phone字段进行安全加密`

## 3. 用户故事
1. 作为平台使用者，我希望 GitHub issue 命中 `github issue template` 后，统一先进入 `issue distribution agent`，由它识别意图并选择执行 agent。
2. 作为平台维护者，我希望当前版本只接入一个 worker agent：`安全加密agent`，但未来可平滑扩展更多 issue worker agent。
3. 作为 issue 发起人，我希望 worker 执行完成后，GitHub issue 能收到评论，看到生成的 Git 分支和 Silicon Agent task URL。
4. 作为研发，我希望 task 能保留 issue 号、issue URL、repo 信息，便于排查和回归验证。
5. 作为仓库协作者，我希望在 issue 评论里通过 `@silicon_agent` 或 `/silicon_agent` 显式触发工作流，而不是只能依赖 issue 创建事件。

## 4. 功能范围
1. 新增内置模板 `github_issue_template`。
2. 新增或标准化两个 agent 角色：
   - `issue distribution agent`
   - `安全加密agent`
3. 所有命中该模板的 GitHub issue 都先进入 distribution stage。
4. distribution stage 基于 issue 内容输出结构化分发结果。
5. 当前 worker 仅支持把安全加密类 issue 分发给 `安全加密agent`。
6. `安全加密agent` 执行完成后，向原始 GitHub issue 回帖：
   - Git 分支名
   - Silicon Agent task URL
7. 支持 GitHub issue comment 事件中的 `@silicon_agent` 与 `/silicon_agent` 命令触发，继续复用同一模板与 worker 链路。

## 5. 验收标准
1. GitHub issue 命中 `github_issue_template` 后，task stages 顺序固定为：
   - `dispatch_issue`
   - `process_security_issue`
2. 第一阶段 agent_role 必须是 `issue distribution agent`。
3. distribution 产出必须显式包含 `selected_agent_role` 等结构化字段，并能把 issue #13 识别为 `安全加密agent`。
4. 真实 webhook 与 mock webhook 两条路径都必须回填 `github_issue_number`。
5. task 必须保留 issue URL、repo_full_name、issue body 等关键上下文。
6. `安全加密agent` 完成后必须尝试回帖 issue；评论内容至少包含分支名和 task URL。
7. 若分支推送失败或评论失败，日志中必须能定位失败原因，不能出现静默成功。
8. 普通 issue 评论不得触发任务；只有命令评论会命中 trigger。
9. 当前版本不做评论人权限校验，任何可评论用户均可触发；该行为必须在文档中标记为高风险默认值。

## 6. 文件路径
### 6.1 预计修改文件
- `app/api/webhooks/github.py`
- `app/services/trigger_service.py`
- `app/services/task_service.py`
- `app/services/template_service.py`
- `app/services/seed_service.py`
- `app/worker/prompts.py`
- `app/worker/agents.py`
- `app/schemas/task.py`
- `tests/test_template_service.py`
- `tests/test_mock_webhook.py`
- `tests/test_webhook_project.py`
- `tests/test_task_service.py`
- `tests/test_prompts.py`
- `tests/test_worker.py`
- `tests/test_agents.py`

### 6.2 本次 spec 文档
- `docs/specs/feature-009-GitHubIssue任务分发工作流/01_requirements.md`
- `docs/specs/feature-009-GitHubIssue任务分发工作流/02_interface.md`
- `docs/specs/feature-009-GitHubIssue任务分发工作流/03_implementation.md`

## 7. 非目标
1. 本轮不做动态 stage 增删。
2. 本轮不实现多个 worker agent 的真正并行分发。
3. 本轮不自动关闭 GitHub issue。
4. 本轮不重构整套 worker graph。
