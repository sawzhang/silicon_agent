# feature-005-项目管理页面能力梳理 - 接口与数据结构

## 1. 页面接口清单

### 1.1 项目管理
```http
GET    /api/v1/projects?page=1&page_size=20&status=active&name=keyword
GET    /api/v1/projects/{project_id}
POST   /api/v1/projects
PUT    /api/v1/projects/{project_id}
DELETE /api/v1/projects/{project_id}
POST   /api/v1/projects/{project_id}/sync
```

### 1.2 项目维度任务联动
```http
GET /api/v1/tasks?page=1&page_size=20&project_id={project_id}&status=running&title=xxx
```

## 2. 核心签名（现状）

### 2.1 Project API
```python
@router.get("", response_model=ProjectListResponse)
async def list_projects(page: int = 1, page_size: int = 20, status: Optional[str] = None, name: Optional[str] = None)

@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(project_id: str)

@router.post("", response_model=ProjectResponse, status_code=201)
async def create_project(request: ProjectCreateRequest)

@router.put("/{project_id}", response_model=ProjectResponse)
async def update_project(project_id: str, request: ProjectUpdateRequest)

@router.post("/{project_id}/sync", response_model=ProjectSyncResponse)
async def sync_project(project_id: str)

@router.delete("/{project_id}", status_code=204)
async def delete_project(project_id: str)
```

### 2.2 ProjectService
```python
class ProjectService:
    async def list_projects(self, page: int = 1, page_size: int = 20, status: Optional[str] = None, name: Optional[str] = None) -> ProjectListResponse
    async def get_project(self, project_id: str) -> Optional[ProjectResponse]
    async def create_project(self, request: ProjectCreateRequest) -> ProjectResponse
    async def update_project(self, project_id: str, request: ProjectUpdateRequest) -> Optional[ProjectResponse]
    async def delete_project(self, project_id: str) -> bool
    async def sync_repo(self, project_id: str) -> Optional[ProjectSyncResponse]
```

### 2.3 TaskService（项目联动）
```python
class TaskService:
    async def list_tasks(self, page: int = 1, page_size: int = 20, status: Optional[str] = None, project_id: Optional[str] = None, title: Optional[str] = None) -> TaskListResponse
```

## 3. 数据结构

### 3.1 ProjectCreateRequest
```json
{
  "name": "project-name",
  "display_name": "项目展示名",
  "repo_url": "https://github.com/org/repo",
  "repo_local_path": "/path/to/repo",
  "branch": "main",
  "description": "项目描述",
  "sandbox_image": "silicon-agent-sandbox:coding"
}
```

### 3.2 ProjectResponse（关键字段）
- `id`, `name`, `display_name`, `status`
- `repo_url`, `repo_local_path`, `branch`, `description`, `sandbox_image`
- `tech_stack`, `repo_tree`, `last_synced_at`
- `created_at`, `updated_at`

### 3.3 ProjectSyncResponse
```json
{
  "tech_stack": ["Python", "FastAPI"],
  "tree_depth": 2,
  "readme_length": 1234,
  "synced_at": "2026-03-02T10:00:00Z"
}
```

## 4. Mock Data（页面常用）

### 4.1 列表请求
```http
GET /api/v1/projects?page=1&page_size=20&status=active&name=platform
```

### 4.2 列表响应
```json
{
  "items": [
    {
      "id": "7e79a5aa-3472-4de3-9aff-57996c6546c7",
      "name": "hello-world",
      "display_name": "hello world",
      "status": "active",
      "branch": "master",
      "repo_url": "https://example.com/repo.git",
      "last_synced_at": "2026-02-28T09:00:00"
    }
  ],
  "total": 1
}
```

### 4.3 任务联动请求
```http
GET /api/v1/tasks?project_id=7e79a5aa-3472-4de3-9aff-57996c6546c7&status=running
```
