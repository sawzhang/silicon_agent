# feature-006-任务管线详情GitPR能力梳理 - 接口与数据结构

## 1. 页面相关接口清单

### 1.1 任务详情
```http
GET /api/v1/tasks/{task_id}
```

### 1.2 任务日志（用于核对 PR 流程事件）
```http
GET /api/v1/task-logs?task_id={task_id}&page=1&page_size=50
```

## 2. 核心签名（现状）

### 2.1 Worker 收尾 PR 流程
```python
# app/worker/engine.py
async def _finalize_task_resources(
    session: AsyncSession,
    task: TaskModel,
    prior_outputs: list[dict],
    sandbox_mode: str | None,
    worktree_mgr: Any,
    worktree_path: str | None,
    workspace_path: str | None,
    workspace_source: str,
    workspace_branch: str | None,
    sandbox_mgr: Any,
    sandbox_info: Any,
) -> bool
```

### 2.2 Worktree / Workspace Git 能力
```python
# app/worker/worktree.py
class WorktreeManager:
    async def commit_and_push(
        self,
        task_id: str,
        commit_message: str,
        target_branch: Optional[str] = None,
    ) -> Optional[str]

    async def create_pr(
        self,
        task_id: str,
        title: str,
        body: str,
        base_branch: str = "main",
    ) -> Optional[str]

async def commit_and_push_workspace(
    *,
    workspace: str,
    commit_message: str,
    target_branch: Optional[str] = None,
) -> Optional[str]

async def create_pr_for_workspace(
    *,
    workspace: str,
    title: str,
    body: str,
    base_branch: str = "main",
) -> Optional[str]
```

### 2.3 任务详情返回链路
```python
# app/services/task_service.py
class TaskService:
    @staticmethod
    def _task_to_response(task: TaskModel) -> TaskDetailResponse

# app/schemas/task.py
class TaskDetailResponse(BaseModel):
    branch_name: Optional[str] = None
    pr_url: Optional[str] = None
```

## 3. 数据结构

### 3.1 任务模型关键字段
- `TaskModel.branch_name: Optional[str]`
- `TaskModel.pr_url: Optional[str]`
- `TaskModel.target_branch: Optional[str]`

### 3.2 系统事件关键类型
- `worktree_commit_push_started`
- `worktree_commit_push_finished`
- `worktree_pr_started`
- `worktree_pr_finished`

## 4. Mock Data

### 4.1 任务详情响应（含 PR 信息）
```json
{
  "id": "89f5980a-fa59-48af-a002-d8632cb4bb44",
  "title": "修复任务管线失败重试逻辑",
  "status": "completed",
  "branch_name": "task/89f5980a-fix-retry",
  "pr_url": "https://github.com/org/repo/pull/123",
  "target_branch": "main",
  "stages": []
}
```

### 4.2 日志响应片段（PR 生命周期）
```json
{
  "items": [
    {
      "event_type": "worktree_pr_started",
      "event_source": "system",
      "status": "running"
    },
    {
      "event_type": "worktree_pr_finished",
      "event_source": "system",
      "status": "success",
      "response_body": {
        "pr_url": "https://github.com/org/repo/pull/123"
      }
    }
  ]
}
```
