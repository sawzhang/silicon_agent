# feature-005-项目管理页面能力梳理

## 1. 背景与目标
按“新需求规格”格式梳理当前项目管理页面已具备能力，形成可评审、可回归、可扩展的基线文档。

## 2. 用户故事
1. 作为项目管理员，我需要查看项目列表，并按状态/名称搜索。
2. 作为项目管理员，我需要创建与编辑项目基础信息（名称、仓库、分支、描述、sandbox镜像）。
3. 作为项目管理员，我需要查看项目详情与仓库同步结果（技术栈、目录树、同步时间）。
4. 作为项目管理员，我需要删除无效项目。
5. 作为任务管理者，我需要按 `project_id` 在任务列表中筛选任务，查看项目维度任务状态。

## 3. 功能范围（当前已实现）
1. 项目 CRUD：
   - 列表 `GET /api/v1/projects`
   - 详情 `GET /api/v1/projects/{project_id}`
   - 创建 `POST /api/v1/projects`
   - 更新 `PUT /api/v1/projects/{project_id}`
   - 删除 `DELETE /api/v1/projects/{project_id}`
2. 项目仓库同步：`POST /api/v1/projects/{project_id}/sync`
3. 列表过滤：
   - `status` 精确过滤
   - `name` 对 `name/display_name` 模糊过滤
4. 与任务管理联动：`GET /api/v1/tasks?project_id=...`

## 4. 验收标准（现状基线）
1. 新建项目成功返回 201，且默认 `status=active`。
2. 同名项目创建冲突返回 409。
3. 项目列表支持分页与筛选，返回 `items + total`。
4. `sync` 成功返回 `tech_stack/tree_depth/readme_length/synced_at`，并落库 `tech_stack/repo_tree/last_synced_at`。
5. 任务列表使用 `project_id` 可筛选对应项目任务。

## 5. 文件路径
### 5.1 已实现代码
- `app/api/v1/projects.py`
- `app/schemas/project.py`
- `app/services/project_service.py`
- `app/models/project.py`
- `app/api/v1/tasks.py`
- `app/services/task_service.py`
- `tests/test_projects_api.py`
- `tests/test_tasks_api.py`

### 5.2 本次文档新增
- `docs/specs/feature-005-项目管理页面能力梳理/01_requirements.md`
- `docs/specs/feature-005-项目管理页面能力梳理/02_interface.md`
- `docs/specs/feature-005-项目管理页面能力梳理/03_implementation.md`

## 6. 非目标
1. 本文不新增权限系统、审批流、项目级实时推送。
2. 本文不改动现有项目同步算法与第三方仓库连接策略。
