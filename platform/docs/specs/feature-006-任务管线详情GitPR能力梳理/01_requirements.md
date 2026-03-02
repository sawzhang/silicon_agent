# feature-006-任务管线详情GitPR能力梳理

## 1. 背景与目标
对“任务管线详情流程”进行现状核查，确认是否已经具备 Git PR 能力；若已具备，按新需求 Spec 格式沉淀为可评审、可追踪、可回归的基线文档。

## 2. 结论（核查结果）
当前项目已具备任务执行完成后的 Git 提交、推送、PR 创建能力，并在任务详情中暴露 `branch_name` 与 `pr_url` 字段。

## 3. 用户故事
1. 作为任务查看者，我希望在任务详情页看到分支名和 PR 链接，快速进入代码评审。
2. 作为运维/研发，我希望通过系统日志确认 PR 流程是否执行（started/finished）。
3. 作为项目负责人，我希望仅在仓库上下文可用时触发 PR 流程，避免无仓库任务误触发。

## 4. 功能范围（当前已实现）
1. 任务收尾阶段执行 SCM 流程：`commit -> push -> create PR`。
2. 支持两种执行路径：
   - worktree 管理路径（`WorktreeManager`）
   - 临时 workspace 路径（`commit_and_push_workspace` + `create_pr_for_workspace`）
3. 持久化与对外返回：`TaskModel.branch_name`、`TaskModel.pr_url`。
4. 任务日志事件：
   - `worktree_commit_push_started` / `worktree_commit_push_finished`
   - `worktree_pr_started` / `worktree_pr_finished`

## 5. 验收标准（现状基线）
1. 当任务绑定项目且存在 `repo_url`、`workspace_path` 时，任务收尾会尝试执行 commit/push。
2. commit/push 成功后会记录 `branch_name`，并继续尝试创建 PR。
3. PR 创建成功时写入 `pr_url`，可通过 `GET /api/v1/tasks/{task_id}` 查询。
4. 任务日志可查询到 PR 流程开始/结束系统事件。
5. commit/push 异常时任务会失败（`_fail_task`），避免状态假成功。

## 6. 文件路径
### 6.1 历史实现文件（已存在）
- `app/worker/engine.py`
- `app/worker/worktree.py`
- `app/models/task.py`
- `app/schemas/task.py`
- `app/services/task_service.py`
- `app/api/v1/tasks.py`
- `tests/test_engine_worktree_and_workspace.py`
- `tests/test_worker_worktree.py`

### 6.2 本次文档新增
- `docs/specs/feature-006-任务管线详情GitPR能力梳理/01_requirements.md`
- `docs/specs/feature-006-任务管线详情GitPR能力梳理/02_interface.md`
- `docs/specs/feature-006-任务管线详情GitPR能力梳理/03_implementation.md`

## 7. 非目标
1. 本文不新增 PR 能力，仅做现状能力归档。
2. 本文不改动 gh CLI、token 注入、工作目录清理策略。
3. 本文不扩展前端交互，仅描述后端能力与可观测结果。
