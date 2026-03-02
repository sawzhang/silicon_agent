# Feature 001 - CI/CD能力盘点

## 1. 背景
- 当前仓库目录为 `.github`，主要承载 GitHub Actions 工作流，不是传统业务代码项目。
- 目标是先沉淀“现有 CI/CD 能力文档”，形成后续优化、补充和治理的基线。

## 2. 用户故事
1. 作为研发成员，我希望快速理解当前 CI 和 E2E 流程覆盖范围，便于评估改动风险。
2. 作为维护者，我希望知道触发条件、执行顺序、失败门禁和产物策略，便于排障和治理。
3. 作为新成员，我希望有统一入口文档，减少阅读 YAML 细节成本。

## 3. 范围
### 3.1 In Scope
- 盘点并文档化以下现有工作流能力：
  - `workflows/ci.yml`
  - `workflows/e2e.yml`
- 记录触发器、并发控制、Job 依赖、质量门禁、密钥依赖、产物策略、失败处理。
- 提供关键接口 Mock 示例（E2E 探活接口的示例请求/响应）。

### 3.2 Out of Scope
- 不修改现有 GitHub Actions 逻辑。
- 不新增发布流水线（如镜像发布、环境部署、回滚）。
- 不调整覆盖率阈值、PR 行数阈值、测试范围等治理策略。

## 4. 验收标准
1. 在 `docs/specs/feature-001-cicd能力盘点/` 下形成 3 份文档（requirements/interface/implementation）。
2. 文档可回答以下问题：
   - 什么时候触发 CI/E2E？
   - 哪些改动会触发 backend/frontend 校验？
   - 失败会阻塞什么？
   - 使用了哪些 secrets 与 artifacts？
3. 文档包含“文件路径、签名、Mock 数据”三个必填信息块。

## 5. 相关文件路径
- `workflows/ci.yml`
- `workflows/e2e.yml`
- `docs/specs/feature-001-cicd能力盘点/01_requirements.md`
- `docs/specs/feature-001-cicd能力盘点/02_interface.md`
- `docs/specs/feature-001-cicd能力盘点/03_implementation.md`
