# feature-005-项目管理页面能力梳理 - 实现细节

## 1. 页面能力流程（现状）
1. 页面初始化调用 `GET /projects` 获取项目列表。
2. 支持按 `status/name` 筛选并刷新列表。
3. 新建项目调用 `POST /projects`，冲突（name 唯一）返回 409。
4. 编辑项目调用 `PUT /projects/{id}`，支持基础元信息和状态更新。
5. 点击“同步仓库”调用 `POST /projects/{id}/sync`，返回同步摘要并更新项目技术栈/目录树。
6. 在项目详情页可调用 `GET /tasks?project_id={id}` 查看关联任务进度。
7. 删除项目调用 `DELETE /projects/{id}`，成功返回 204。

## 2. 后端实现要点

### 2.1 列表与过滤
- `ProjectService.list_projects`：
  - status 精确过滤
  - name 对 `name/display_name` 做 `lower + like` 模糊检索
  - 按 `created_at DESC` 排序
  - 分页通过 `offset/limit`

### 2.2 创建与更新
- `create_project` 直接写入 `ProjectModel` 并 refresh。
- `update_project` 采用“字段非空才更新”的增量写法。
- 路由层将 `IntegrityError` 映射为 409（项目名称冲突）。

### 2.3 仓库同步
- `sync_repo` 依赖 `repo_analyzer.analyze_repo`：
  - repo_url 为空时抛 400
  - 仓库不存在映射为 400
  - 成功后更新：`tech_stack`, `repo_tree`, `last_synced_at`
- 返回 `ProjectSyncResponse`：`tech_stack/tree_depth/readme_length/synced_at`

### 2.4 与任务管理联动
- `TaskService.list_tasks` 支持 `project_id` 过滤。
- 页面可复用任务列表能力做项目维度看板。

## 3. 当前边界与已知限制
1. 项目管理无独立 WebSocket 实时事件（主要依赖轮询刷新）。
2. `ProjectListResponse` 当前仅返回 `items + total`，不含 `page/page_size`。
3. 删除项目不包含“二次确认/软删除”机制。
4. 项目与任务统计聚合（如每项目运行任务数）未在项目接口内直接提供。

## 4. 测试覆盖证据
- `tests/test_projects_api.py`
  - 创建/详情/列表/更新/删除
  - 名称模糊搜索
  - 分页
- `tests/test_tasks_api.py`
  - `project_id` 过滤任务列表

## 5. 后续扩展建议（文档层）
1. 若项目页新增统计卡片，建议新增聚合接口或在 `GET /projects` 提供可选扩展字段。
2. 若增加实时刷新，建议补充 `project` 维度 WS 事件并在本 Spec 更新事件协议。
3. 若引入归档流程，需补 `status` 状态机与删除策略说明。
