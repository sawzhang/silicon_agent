# feature-006-任务管线详情GitPR能力梳理 - 实现细节

## 1. 核查结论
任务管线详情流程已实现 Git PR 相关能力，触发点位于 `engine._finalize_task_resources` 的 SCM finalize 阶段。

## 2. 执行流程（现状）
1. 进入收尾阶段后判断仓库上下文：`repo_url && workspace_path`。
2. 记录 `worktree_commit_push_started` 系统日志。
3. 执行 commit/push：
   - 若存在 `worktree_mgr + worktree_path`，调用 `worktree_mgr.commit_and_push(...)`
   - 否则调用 `commit_and_push_workspace(...)`
4. commit/push 成功后：
   - 记录 `worktree_commit_push_finished`
   - 将返回分支名持久化到 `task.branch_name`
5. 若有分支名，进入 PR 阶段：
   - 记录 `worktree_pr_started`
   - worktree 路径调用 `worktree_mgr.create_pr(...)`
   - workspace 路径调用 `create_pr_for_workspace(...)`
6. PR 成功时写入 `task.pr_url` 并提交事务。
7. 记录 `worktree_pr_finished`，`response_body` 包含 `pr_url`（可为空，表示 PR 创建未成功但流程走完）。

## 3. 关键实现要点

### 3.1 触发前置条件
- 任务必须关联项目且项目有 `repo_url`。
- 运行期必须拿到 `workspace_path`。
- base branch 使用 `task.project.branch or "main"`。

### 3.2 分支策略
- 优先使用 `target_branch`。
- 未指定时按任务生成分支名（`task/<task_id_prefix>-<slug>`）。
- `branch_name` 在 push 成功后回写任务详情。

### 3.3 PR 创建策略
- 统一通过 `gh pr create`。
- 支持 GitHub/GitHub Enterprise（`GH_HOST`、`GH_ENTERPRISE_TOKEN`）。
- 返回值为 PR URL，成功后持久化到任务。

### 3.4 异常与状态
- commit/push 异常会触发 `_fail_task`，任务整体失败并提前返回。
- PR 创建返回 `None` 时不抛出任务失败，但 `pr_url` 为空，可通过日志排查。

## 4. 可观测性与任务详情映射
1. 页面可通过 `GET /api/v1/tasks/{id}` 获取 `branch_name/pr_url`。
2. 页面可通过日志接口查看 PR 生命周期事件。
3. 回归可用测试覆盖：
   - `tests/test_engine_worktree_and_workspace.py::test_finalize_task_resources_worktree_commit_and_pr`
   - `tests/test_engine_worktree_and_workspace.py::test_finalize_task_resources_workspace_commit_and_pr`
   - `tests/test_engine_worktree_and_workspace.py::test_finalize_task_resources_worktree_commit_exception`
   - `tests/test_worker_worktree.py::test_create_pr_for_workspace_variants`

## 5. 已知边界
1. PR 创建依赖运行环境中的 `gh` CLI 可用性与凭据配置。
2. 当 `pr_url` 为空但任务完成时，需要结合 `worktree_pr_finished` 日志定位失败原因。
3. 当前未引入“PR 必须成功才能完成任务”的强约束。
