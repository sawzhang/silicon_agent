# Feature 002 - 编译检查与单元测试

## 1. 背景
- `feature-001` 已完成现状盘点，本特性用于定义下一步 CI 质量门禁能力。
- 当前前端缺少单元测试执行能力；后端虽有测试执行，但未明确“单元测试门禁”与“编译检查门禁”的结构化定义。

## 2. 用户故事
1. 作为开发者，我希望 PR 阶段能自动执行编译检查，避免不可构建代码进入主干。
2. 作为开发者，我希望 PR 阶段执行稳定、快速的单元测试，尽早发现回归问题。
3. 作为维护者，我希望前后端门禁规则对齐且可观测，便于治理和追责。

## 3. 范围
### 3.1 In Scope
- 设计并文档化以下能力：
  - 后端编译检查（Python 语法级检查）
  - 前端编译检查（TypeScript + Vite build）
  - 后端单元测试门禁
  - 前端单元测试门禁（新增测试框架）
- 明确 Job 触发条件、依赖关系、阈值、失败策略、产物策略。

### 3.2 Out of Scope
- 不在本阶段实现部署/发布流水线。
- 不在本阶段引入 E2E 深度业务场景。
- 不在本阶段强制改造所有历史测试用例。

## 4. 验收标准
1. 在 `docs/specs/feature-002-编译检查与单元测试/` 下形成 3 份文档（requirements/interface/implementation）。
2. 文档必须包含：
   - 文件路径（精确到文件）
   - 关键签名（workflow/job/命令）
   - Mock 数据（至少包含成功与失败示例）
3. 文档可直接指导后续实现，不依赖口头补充。

## 5. 相关文件路径（拟改动）
- `.github/workflows/ci.yml`
- `platform/pyproject.toml`
- `web/package.json`
- `web/vitest.config.ts`（新增）
- `web/src/**/*.test.ts` 或 `web/src/**/*.test.tsx`（新增/渐进补齐）
- `docs/specs/feature-002-编译检查与单元测试/01_requirements.md`
- `docs/specs/feature-002-编译检查与单元测试/02_interface.md`
- `docs/specs/feature-002-编译检查与单元测试/03_implementation.md`
