# Feature 001 - 接口与结构

## 1. 技术栈与运行环境
- CI 引擎：GitHub Actions
- 后端检查环境：Python 3.11（`actions/setup-python@v5`）
- 前端检查环境：Node.js 20（`actions/setup-node@v4`）
- 容器化 E2E：Docker Compose（在 `platform` 目录执行）
- 复用组件：
  - `dorny/paths-filter@v3`
  - `trufflesecurity/trufflehog@v3.88.0`
  - `webfactory/ssh-agent@v0.9.0`
  - `py-cov-action/python-coverage-comment-action@v3`
  - `actions/upload-artifact@v4`

## 2. 文件路径（精确）
- `workflows/ci.yml`：主 CI 流程（lint/test/build/secret-scan/pr-size-check）
- `workflows/e2e.yml`：E2E Smoke 流程（容器拉起 + 接口探活）

## 3. 工作流“签名”（Workflow Signatures）
> 注：此处“签名”定义为可稳定识别的 workflow/job/触发与输入输出契约。

### 3.1 CI Workflow
- Workflow Name: `CI`
- Trigger Signature:
  - `push.branches = [master]`
  - `pull_request.branches = [master]`
  - `workflow_dispatch`
- Permission Signature:
  - `contents: read`
  - `pull-requests: write`
- Concurrency Signature:
  - `group: ci-${{ github.ref }}`
  - `cancel-in-progress: true`

#### Job Signatures
1. `jobs.changed-scope`
   - outputs:
     - `backend: steps.filter.outputs.backend`
     - `frontend: steps.filter.outputs.frontend`
   - path filter:
     - backend: `platform/**`
     - frontend: `web/**`
2. `jobs.pr-size-check`
   - 条件：`github.event_name == 'pull_request'`
   - 规则：代码增删行（排除 lock 文件）超过 `1000` 直接失败
3. `jobs.secret-scan`
   - 规则：TruffleHog 扫描 verified + unknown
4. `jobs.backend-lint`
   - 条件：`backend changed || workflow_dispatch`
   - 校验：`ruff check app/ tests/`
5. `jobs.backend-compile-check`
   - 条件：`backend changed || workflow_dispatch`
   - 校验：`python -m compileall -q app tests`
6. `jobs.backend-test`
   - 条件：`backend changed || workflow_dispatch`
   - 校验：`pytest ... --cov-fail-under=80`
   - 输出：PR coverage comment + `coverage-report` artifact
7. `jobs.frontend-check`
   - 条件：`frontend changed || workflow_dispatch`
   - 校验：`tsc --noEmit` + `npm run build`
8. `jobs.frontend-unit-test`
   - 条件：`frontend changed || workflow_dispatch`
   - 校验：`npm run test:unit:ci`

### 3.2 E2E Workflow
- Workflow Name: `E2E Smoke Test`
- Trigger Signature:
  - `push.branches = [master]`
  - `workflow_dispatch`
- Concurrency Signature:
  - `group: e2e-${{ github.ref }}`
  - `cancel-in-progress: true`
- Job Signature:
  - `jobs.e2e-smoke`
  - timeout: `10 minutes`
  - env: `COMPOSE_PROJECT_NAME=e2e`
  - 关键步骤：
    - docker compose build/up
    - `/health` 等接口轮询检查
    - 失败时输出 compose logs
    - always cleanup (`docker compose down -v`)

## 4. 密钥与产物契约
### 4.1 Secrets
- `secrets.SSH_PRIVATE_KEY`：用于私有依赖拉取（backend jobs + e2e job）
- `secrets.GITHUB_TOKEN`：用于 PR coverage comment

### 4.2 Artifacts
- Name: `coverage-report`
- Path:
  - `platform/htmlcov/`
  - `platform/coverage.xml`
- Retention: `30 days`

## 5. Mock 数据（关键接口）
### 5.1 changed-scope 输出（Mock）
```json
{
  "backend": "true",
  "frontend": "false"
}
```

### 5.2 E2E 探活接口（Mock）
```http
GET /health HTTP/1.1
Host: localhost:8000
```

```json
{
  "status": "ok"
}
```

```http
GET /api/v1/agents HTTP/1.1
Host: localhost:8000
```

```json
{
  "items": [],
  "total": 0
}
```

```http
GET /api/v1/templates HTTP/1.1
Host: localhost:8000
```

```json
{
  "items": [],
  "total": 0
}
```

```http
GET /api/v1/tasks HTTP/1.1
Host: localhost:8000
```

```json
{
  "items": [],
  "total": 0
}
```

```http
GET /api/v1/projects HTTP/1.1
Host: localhost:8000
```

```json
{
  "items": [],
  "total": 0
}
```
