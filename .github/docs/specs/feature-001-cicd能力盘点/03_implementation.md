# Feature 001 - 实现逻辑说明（现状）

## 1. 目标
- 解释当前 `.github/workflows` 的执行逻辑和门禁行为。
- 为后续 CI/CD 演进提供“可对照基线”。

## 2. CI 逻辑流（`workflows/ci.yml`）
1. 触发：`push master`、`pull_request master`、`workflow_dispatch`
2. 并发控制：同一 `github.ref` 仅保留最新一次运行。
3. 先执行 `changed-scope`，根据改动路径生成 backend/frontend 标记。
4. 与改动范围无关的全局门禁：
   - `pr-size-check`（仅 PR）：增删行 >1000 失败
   - `secret-scan`：密钥扫描失败则阻塞
5. backend 相关改动触发：
   - `backend-lint`（ruff）
   - `backend-compile-check`（python compileall）
   - `backend-test`（pytest + coverage>=80）
6. frontend 相关改动触发：
   - `frontend-check`（type check + build）
   - `frontend-unit-test`（npm run test:unit:ci）
7. 可观测性产出：
   - PR Coverage Comment
   - `coverage-report` artifact
   - Job Summary 中覆盖率表格

## 3. E2E 逻辑流（`workflows/e2e.yml`）
1. 触发：`push master`、`workflow_dispatch`
2. 拉起容器：
   - `docker compose build --ssh default`
   - `docker compose up -d --wait`
3. 健康检查：
   - 60 秒窗口内轮询 `GET /health`
   - 超时则失败并输出 app 日志
4. Smoke 接口检查（期望 HTTP 200）：
   - `/health`
   - `/api/v1/agents`
   - `/api/v1/templates`
   - `/api/v1/tasks`
   - `/api/v1/projects`
5. 失败行为：
   - 记录失败用例数量
   - 输出 compose logs（`failure()`）
6. 收尾行为：
   - 始终 `docker compose down -v`

## 4. 风险与当前约束
- 当前仅覆盖 `master`，未定义 release/hotfix 分支策略。
- 仅有 smoke 级别接口探活，缺少更深层业务断言。
- 覆盖率阈值固定 80%，尚未按模块分层管理。
- `SSH_PRIVATE_KEY` 为关键依赖，密钥不可用会导致多个 Job 连锁失败。

## 5. 建议的后续迭代（非本次实现）
1. 增加 CI 状态徽章和统一失败排障指南文档。
2. 为 E2E 增加最小写操作用例（创建+查询），提高回归价值。
3. 引入可选的 nightly 全量回归工作流，和 PR 快速检查解耦。

## 6. 本 Spec 对应文件清单
- 已分析：
  - `workflows/ci.yml`
  - `workflows/e2e.yml`
- 已产出：
  - `docs/specs/feature-001-cicd能力盘点/01_requirements.md`
  - `docs/specs/feature-001-cicd能力盘点/02_interface.md`
  - `docs/specs/feature-001-cicd能力盘点/03_implementation.md`
