# Feature 002 - 接口与结构设计

## 1. 技术与工具
- CI 引擎：GitHub Actions
- 后端：Python 3.11 + pytest + ruff
- 前端：Node.js 20 + TypeScript + Vite + Vitest（新增）

## 2. 文件路径（精确）
- `.github/workflows/ci.yml`
- `platform/pyproject.toml`
- `web/package.json`
- `web/vitest.config.ts`（新增）

## 3. 工作流签名（目标态）

### 3.1 Workflow Signature
- Workflow Name: `CI`
- Trigger Signature:
  - `push.branches = [master]`
  - `pull_request.branches = [master]`
  - `workflow_dispatch`

### 3.2 Job Signatures
1. `jobs.backend-compile-check`
   - if: `backend changed || workflow_dispatch`
   - working-directory: `platform`
   - command signature:
     - `python -m compileall -q app tests`

2. `jobs.backend-unit-test`
   - if: `backend changed || workflow_dispatch`
   - working-directory: `platform`
   - command signature:
     - `pytest tests/ -v --tb=short --cov=app --cov-report=xml --cov-fail-under=80`
   - output signature:
     - coverage summary 写入 `$GITHUB_STEP_SUMMARY`
     - 上传 `coverage-report` artifact

3. `jobs.frontend-compile-check`
   - if: `frontend changed || workflow_dispatch`
   - working-directory: `web`
   - command signature:
     - `npm ci`
     - `npm run build`

4. `jobs.frontend-unit-test`
   - if: `frontend changed || workflow_dispatch`
   - working-directory: `web`
   - command signature:
     - `npm ci`
     - `npm run test:unit -- --run`
   - output signature:
     - 输出测试通过/失败统计
     - 可选上传 `coverage` artifact（后续实现决定）

## 4. 配置签名

### 4.1 `web/package.json`
- scripts 新增签名：
  - `"test:unit": "vitest"`
  - `"test:unit:ci": "vitest --run --coverage"`

### 4.2 `web/vitest.config.ts`
- 导出签名：
  - `defineConfig({ test: { environment: "jsdom", globals: true, coverage: { reporter: ["text", "lcov"] }}})`

### 4.3 `platform/pyproject.toml`
- pytest（可选增强）签名：
  - `addopts` 增加统一失败策略（如 `-q --maxfail=1`）
  - 可选 marker 约定（`unit` / `integration`），用于后续分层执行

## 5. Mock 数据

### 5.1 前端单测通过（Mock）
```text
Test Files  12 passed (12)
Tests       58 passed (58)
Duration    7.12s
```

### 5.2 前端单测失败（Mock）
```text
Test Files  11 passed | 1 failed (12)
Tests       56 passed | 2 failed (58)
Error: expected "200" to be "201"
```

### 5.3 后端编译检查失败（Mock）
```text
*** Error compiling 'app/api/routes/task.py'...
  File "app/api/routes/task.py", line 97
    return {"ok": True
                      ^
SyntaxError: '{' was never closed
```
