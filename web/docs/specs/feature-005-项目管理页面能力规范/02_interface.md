# Feature-005 项目管理页面能力规范 - 接口与数据结构

## 1. 关键类型（当前）
文件：`src/types/project.ts`

```ts
export interface Project {
  id: string;
  name: string;
  display_name: string;
  repo_url: string | null;
  branch: string;
  description: string | null;
  status: 'active' | 'archived';
  tech_stack: string[] | null;
  repo_tree: string | null;
  last_synced_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ProjectCreateRequest {
  name: string;
  display_name: string;
  repo_url?: string;
  branch?: string;
  description?: string;
}

export interface ProjectUpdateRequest {
  display_name?: string;
  repo_url?: string;
  branch?: string;
  description?: string;
  status?: string;
}

export interface ProjectSyncResponse {
  tech_stack: string[];
  tree_depth: number;
  readme_length: number;
  synced_at: string;
}
```

## 2. API 签名（当前）
文件：`src/services/projectApi.ts`

```ts
export async function listProjects(params?: {
  page?: number;
  page_size?: number;
  status?: string;
  name?: string;
}): Promise<ProjectListResponse>

export async function createProject(req: ProjectCreateRequest): Promise<Project>

export async function updateProject(id: string, req: ProjectUpdateRequest): Promise<Project>

export async function deleteProject(id: string): Promise<void>

export async function syncProject(id: string): Promise<ProjectSyncResponse>
```

## 3. Hook 签名（当前）
文件：`src/hooks/useProjects.ts`

```ts
export function useProjectList(params?: {
  page?: number;
  page_size?: number;
  status?: string;
  name?: string;
})

export function useCreateProject()
export function useDeleteProject()
export function useUpdateProject()
```

说明：`useUpdateProject` 已实现，新增需求要求在项目管理页接入。

## 4. 页面交互接口
文件：`src/pages/Projects/index.tsx`

```ts
const handleSync = async (projectId: string) => Promise<void>
```

- 同步接口调用：`syncProject(projectId)`
- 新建项目提交：`createProject.mutateAsync(...)`
- 编辑项目提交：`updateProject(id, req)` / `useUpdateProject().mutateAsync(...)`
- 删除项目提交：`deleteProject.mutateAsync(record.id)`

## 5. Mock 数据

### 5.1 列表查询 `GET /projects`
```json
{
  "items": [
    {
      "id": "proj_001",
      "name": "silicon_agent",
      "display_name": "Silicon Agent",
      "repo_url": "https://github.com/org/silicon-agent",
      "branch": "main",
      "description": "Agent orchestration project",
      "status": "active",
      "tech_stack": ["Python", "FastAPI", "React", "TypeScript"],
      "repo_tree": null,
      "last_synced_at": "2026-03-02T07:10:00Z",
      "created_at": "2026-02-20T08:00:00Z",
      "updated_at": "2026-03-02T07:10:00Z"
    }
  ],
  "total": 1
}
```

### 5.2 新建项目 `POST /projects`
请求：
```json
{
  "name": "platform",
  "display_name": "Platform",
  "repo_url": "https://github.com/org/platform",
  "branch": "main",
  "description": "Backend services"
}
```
响应：
```json
{
  "id": "proj_002",
  "name": "platform",
  "display_name": "Platform",
  "repo_url": "https://github.com/org/platform",
  "branch": "main",
  "status": "active"
}
```

### 5.3 同步仓库 `POST /projects/{id}/sync`
响应：
```json
{
  "tech_stack": ["Python", "FastAPI", "SQLAlchemy"],
  "tree_depth": 6,
  "readme_length": 1240,
  "synced_at": "2026-03-02T07:20:00Z"
}
```

### 5.4 编辑项目 `PUT /projects/{id}`
请求：
```json
{
  "display_name": "Silicon Agent Platform",
  "repo_url": "https://github.com/org/silicon-agent",
  "branch": "develop",
  "description": "Updated project description",
  "status": "active"
}
```
响应：
```json
{
  "id": "proj_001",
  "name": "silicon_agent",
  "display_name": "Silicon Agent Platform",
  "repo_url": "https://github.com/org/silicon-agent",
  "branch": "develop",
  "description": "Updated project description",
  "status": "active",
  "updated_at": "2026-03-02T08:10:00Z"
}
```

### 5.5 删除项目 `DELETE /projects/{id}`
响应：`204 No Content`
