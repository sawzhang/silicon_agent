# feature-009-GitHubIssue任务分发工作流 - 接口与数据结构

## 1. 相关接口

### 1.1 GitHub 项目级 webhook
```http
POST /webhooks/github/{project_id}
```

### 1.2 Mock webhook
```http
POST /api/v1/projects/{project_id}/mock-webhook
```

### 1.3 查询任务详情
```http
GET /api/v1/tasks/{task_id}
```

### 1.4 查询模板
```http
GET /api/v1/templates
```

## 2. 核心签名

### 2.1 GitHub payload 标准化
```python
# app/api/webhooks/github.py
def _normalize_github_payload(gh_event: str, event_type: str, body: dict) -> dict
```

### 2.2 触发器任务创建
```python
# app/services/trigger_service.py
class TriggerService:
    async def process_event(
        self,
        source: str,
        event_type: str,
        payload: dict,
        project_id: Optional[str] = None,
    ) -> Optional[str]
```

### 2.3 任务创建
```python
# app/services/task_service.py
class TaskService:
    async def create_task(self, request: TaskCreateRequest) -> TaskDetailResponse
```

### 2.4 内置模板 seed
```python
# app/services/template_service.py
class TemplateService:
    async def seed_builtin_templates(self) -> None
```

## 3. 模板结构

### 3.1 github_issue_template
```json
{
  "name": "github_issue_template",
  "display_name": "Github Issue",
  "description": "GitHub issue 统一入口模板，先分发再执行",
  "stages": [
    {
      "name": "dispatch_issue",
      "agent_role": "issue distribution",
      "order": 0,
      "instruction": "读取 GitHub issue 上下文并输出结构化分发结果"
    },
    {
      "name": "des encrypt",
      "agent_role": "des encrypt",
      "order": 1,
      "instruction": "基于 dispatch 产出执行安全加密改造并回帖 GitHub issue"
    }
  ],
  "gates": []
}
```

## 4. Distribution 输出协议

### 4.1 结构化字段
```json
{
  "selected_agent_role": "des encrypt",
  "intent": "security_encryption",
  "issue_number": 13,
  "issue_url": "https://scm.starbucks.com/china/starbucks-asg-api/issues/13",
  "repo_full_name": "china/starbucks-asg-api",
  "task_title": "处理 GitHub Issue #13: 安全加密",
  "work_summary": "对本项目的 phone 字段接入安全加密改造",
  "acceptance_criteria": [
    "代码已完成 phone 字段加密接入",
    "变更已推送到远端分支",
    "Issue 已回帖 task URL 与分支信息"
  ],
  "dispatch_reason": "issue 标题和正文均明确要求安全加密 agent 处理 phone 字段"
}
```

## 5. 任务数据

### 5.1 TaskCreateRequest 关键字段
```python
class TaskCreateRequest(BaseModel):
    title: str
    description: Optional[str] = None
    template_id: Optional[str] = None
    project_id: Optional[str] = None
    github_issue_number: Optional[int] = None
```

### 5.2 TaskDetailResponse 关键字段
```python
class TaskDetailResponse(BaseModel):
    id: str
    title: str
    description: Optional[str]
    github_issue_number: Optional[int]
    branch_name: Optional[str]
    pr_url: Optional[str]
    stages: list[TaskStageResponse]
```

## 6. Mock Data

### 6.1 GitHub issue 标准化 payload
```json
{
  "event_type": "issue_opened",
  "repo_full_name": "china/starbucks-asg-api",
  "repo_name": "starbucks-asg-api",
  "issue_number": 13,
  "issue_title": "安全加密",
  "issue_body": "安全加密agent，对本项目的phone字段进行安全加密",
  "issue_url": "https://scm.starbucks.com/china/starbucks-asg-api/issues/13",
  "issue_author": "jowang",
  "title": "安全加密",
  "author": "jowang",
  "labels": []
}
```

### 6.2 GitHub issue comment 命令标准化 payload
```json
{
  "event_type": "issue_comment_created",
  "repo_full_name": "china/starbucks-asg-api",
  "issue_number": 13,
  "issue_title": "安全加密",
  "issue_body": "安全加密agent，对本项目的phone字段进行安全加密",
  "issue_url": "https://scm.starbucks.com/china/starbucks-asg-api/issues/13",
  "comment_id": 1001,
  "comment_url": "https://scm.starbucks.com/china/starbucks-asg-api/issues/13#issuecomment-1001",
  "comment_body": "@silicon_agent 只处理 phone 字段",
  "comment_author": "jowang",
  "silicon_agent_command_triggered": true,
  "silicon_agent_command_style": "mention",
  "silicon_agent_command_text": "@silicon_agent",
  "silicon_agent_command_note": "只处理 phone 字段"
}
```

### 6.3 任务详情响应
```json
{
  "id": "task-123",
  "title": "处理 GitHub Issue #13: 安全加密",
  "description": "Issue URL: https://scm.starbucks.com/china/starbucks-asg-api/issues/13\nRepo: china/starbucks-asg-api\nBody: 安全加密agent，对本项目的phone字段进行安全加密",
  "github_issue_number": 13,
  "branch_name": "silicon_agent/abc123",
  "stages": [
    {"stage_name": "dispatch_issue", "agent_role": "issue distribution"},
    {"stage_name": "des encrypt", "agent_role": "des encrypt"}
  ]
}
```

### 6.4 GitHub issue comment 触发规则示例
```json
{
  "source": "github",
  "event_type": "issue_comment_created",
  "filters": {
    "op": "and",
    "conditions": [
      {
        "type": "field_equals",
        "field": "silicon_agent_command_triggered",
        "value": true
      }
    ]
  },
  "dedup_key_template": "github:{repo_full_name}:issue_comment:{comment_id}"
}
```
