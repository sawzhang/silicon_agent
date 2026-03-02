# Feature 002 - 实施方案

## 1. 总体目标
- 在现有 CI 上新增并强化“编译检查 + 单元测试”双门禁。
- 保持按改动范围触发，避免无关任务拖慢反馈。

## 2. 目标流程（CI）
1. `changed-scope` 输出 backend/frontend 改动标记。
2. backend 改动触发：
   - `backend-compile-check`
   - `backend-unit-test`
3. frontend 改动触发：
   - `frontend-compile-check`
   - `frontend-unit-test`
4. PR 场景保留：
   - `pr-size-check`
   - `secret-scan`
5. 任一门禁失败即阻断合并。

## 3. 详细实施步骤

### Step 1: 后端编译检查
- 修改文件：`.github/workflows/ci.yml`
- 新增 Job：`backend-compile-check`
- 核心命令：`python -m compileall -q app tests`
- 价值：在执行 pytest 前先发现语法级错误，减少无效测试开销。

### Step 2: 后端单元测试门禁标准化
- 修改文件：`.github/workflows/ci.yml`
- 调整/保留 Job：`backend-unit-test`（可沿用当前 `backend-test`）
- 核心命令：`pytest tests/ -v --tb=short --cov=app --cov-fail-under=80`
- 输出：保留 PR coverage comment 与 coverage artifact。

### Step 3: 前端单元测试能力补齐
- 修改文件：
  - `web/package.json`
  - `web/vitest.config.ts`（新增）
  - `.github/workflows/ci.yml`
- 新增脚本：
  - `test:unit`
  - `test:unit:ci`
- 新增 Job：`frontend-unit-test`
- 说明：当前仓库无前端测试脚本，此步是能力补齐关键路径。

### Step 4: 前端编译检查与单测解耦
- 修改文件：`.github/workflows/ci.yml`
- Job 划分：
  - `frontend-compile-check` 仅负责编译
  - `frontend-unit-test` 仅负责测试
- 价值：失败定位更明确，减少排障时间。

## 4. 风险与应对
- 风险：前端历史代码缺少可测性，初次引入单测失败率高。
  - 应对：先要求新增/改动代码补测试，历史模块渐进补齐。
- 风险：CI 时长增加。
  - 应对：继续使用 `changed-scope`，并视情况启用缓存与并行。
- 风险：覆盖率阈值导致短期阻塞。
  - 应对：保持后端 80% 阈值，前端先不强制全局阈值，后续逐步收紧。

## 5. 验证策略
- 在测试分支创建 4 类变更验证触发行为：
  1. 仅改 `platform/**`：只触发 backend 两个新门禁。
  2. 仅改 `web/**`：只触发 frontend 两个新门禁。
  3. 同时改动：四个门禁均触发。
  4. 仅文档改动：四个门禁均不触发（保留全局门禁）。

## 6. 回滚策略
- 若引入后出现大面积误报，可先在 `ci.yml` 中将新增 Job 标记为非必需（临时），保留日志采集。
- 修复测试稳定性后再恢复强制门禁。

## 7. 实施状态（2026-03-03）
- 已完成：
  - `.github/workflows/ci.yml` 新增 `backend-compile-check`
  - `.github/workflows/ci.yml` 新增 `frontend-unit-test`
  - `web/package.json` 新增 `test:unit` 与 `test:unit:ci` 脚本
  - `web/vitest.config.ts` 已新增并统一 `jsdom`/coverage 策略
  - `backend-test` / `frontend-check` 的步骤命名已更新为更明确语义
- 本次落地取舍：
  - `frontend-unit-test` 使用 `npm run test:unit:ci`，固定脚本入口，减少命令漂移
  - `@vitest/coverage-v8` 已与 `vitest` 版本对齐，降低 CI 兼容风险
- 后续建议：
  - 增加最小前端单测样例（渲染与 API mock 各 1 条），验证流水线稳定性
  - 根据基线数据再决定是否引入前端覆盖率阈值门禁
