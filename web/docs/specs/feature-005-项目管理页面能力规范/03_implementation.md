# Feature-005 项目管理页面能力规范 - 实现细节

## 1. 页面结构
文件：`src/pages/Projects/index.tsx`

1. `ProTable<Project>` 作为主列表。
2. 工具栏按钮：`新建项目`。
3. 行级操作：`编辑`、`同步`、`删除`。
4. `ModalForm` 承载新建项目表单。
5. `ModalForm`/`DrawerForm`（二选一）承载编辑项目表单。

## 2. 列表加载与筛选
1. `ProTable.request` 调用 `listProjects`。
2. 透传参数：
   - `status -> params.status`
   - `page -> params.current`
   - `page_size -> params.pageSize`
3. 返回映射：`{ data: res.items, total: res.total, success: true }`。

## 3. 新建项目流程
1. 点击“新建项目”打开 `ModalForm`。
2. 提交时调用 `createProject.mutateAsync`。
3. 成功：toast + `actionRef.reload()` + 关闭弹窗。
4. 失败：展示后端 `detail`。

## 4. 删除项目流程
1. 删除按钮位于行级操作区。
2. `Popconfirm` 二次确认后调用 `deleteProject.mutateAsync`。
3. 成功后提示并刷新列表。

## 5. 编辑项目流程
1. 行级“编辑”按钮打开编辑表单并回填当前项目值。
2. 提交时调用 `useUpdateProject().mutateAsync({ id, req })`。
3. 更新请求采用增量字段提交（仅发送变更字段或允许全字段提交）。
4. 成功后提示并刷新列表，失败展示后端 `detail`。

## 6. 同步仓库流程
1. 行级“同步”按钮调用 `handleSync(projectId)`。
2. 同步中状态：`syncingIds` 记录并展示 `SyncOutlined spin`。
3. 成功提示：`同步成功：{tech_stack.join(', ')}`。
4. 同步按钮禁用条件：`!record.repo_url`。

## 7. 状态与展示规则
1. 项目状态标签：
   - `active -> success`
   - `archived -> default`
2. 技术栈使用彩色 `Tag` 展示，未命中预定义色则 `default`。
3. `last_synced_at` 为空展示 `-`。

## 8. 已知边界
1. 表格未透传关键字检索（`name`）参数。
2. 同步结果仅 toast 展示，不保留历史记录视图。
3. 若后端后续扩展 `sandbox_image/repo_local_path`，编辑表单需同步补充字段。

## 9. 回归检查清单
1. 新建项目：成功/后端校验失败。
2. 编辑项目：成功更新/后端校验失败/取消编辑。
3. 删除项目：确认删除/取消删除。
4. 同步项目：有仓库地址成功同步、无仓库地址禁用。
5. 状态筛选：active/archived。
