# GitHub Issue Template Workflow Design

## 背景
当前仓库已经具备以下零散能力：
- GitHub / GHE webhook 接入与 payload 归一化
- task / template / trigger / worker 基础执行链路
- `安全加密agent`、`des_encrypt`、`github_issue_feedback` 的雏形
- Git 分支推送与 PR 创建能力

但这些能力还没有收敛成一套可稳定执行的 “GitHub issue -> distribution agent -> worker agent -> 回帖 issue” 闭环。现状的主要问题是：
- 模板、触发器、agent、skill 的协议不一致
- `github_issue_number` 只在 mock 流程中稳定回填，真实 webhook 未闭环
- `issue distribution agent` 还未成为统一入口
- 当前 `安全加密agent` 依赖脚本和 prompt 零散拼装，缺少内置模板约束

## 真实样本
2026-03-21 通过 GHE API 拉取 `china/starbucks-asg-api#13` 的内容如下：
- 标题：`安全加密`
- 正文：`安全加密agent，对本项目的phone字段进行安全加密`
- URL：`https://scm.starbucks.com/china/starbucks-asg-api/issues/13`

该 issue 是本次首个真实验收样本，目标路由结果应为：
- 命中 `github issue template`
- 统一进入 `issue distribution agent`
- 识别为安全加密类需求
- 分发给 `安全加密agent`
- worker 完成代码提交、推送分支、回帖 task URL 与分支名

## 方案选择
本次采用 “单模板入口 + 固定两阶段链路” 方案。

### 方案说明
1. 新增一个统一模板 `github_issue_template`
2. 模板内固定两个阶段：
   - `dispatch_issue` -> `issue distribution agent`
   - `process_security_issue` -> `安全加密agent`
3. 所有命中该模板的 GitHub issue 都先进入 distribution stage
4. distribution stage 必须输出结构化分发结果
5. 当前版本只支持一个 worker agent：`安全加密agent`
6. 当 distribution 识别结果不是安全加密时，先产出明确的“不支持/待扩展”说明，不动态增删 stage

### 采用该方案的原因
- 与用户要求一致：统一进入 distribution agent
- 能复用当前引擎，不需要先改动态编排核心
- 便于未来增加更多 worker agent 时扩展 dispatch 协议
- 风险可控，适合以 issue #13 为真实样本先落地

## 目标能力
### 1. 统一入口模板
新增内置模板 `github_issue_template`，作为 GitHub issue 自动任务的标准入口。

### 2. 两类 Agent 角色
- `issue distribution agent`
- `安全加密agent`

### 3. 分发输出协议
`issue distribution agent` 必须输出结构化结果，至少包含：
- `selected_agent_role`
- `intent`
- `issue_number`
- `issue_url`
- `repo_full_name`
- `task_title`
- `work_summary`
- `acceptance_criteria`
- `dispatch_reason`

### 4. worker 执行闭环
`安全加密agent` 必须严格按对应 skill 执行：
- 基于 `des_encrypt` skill 修改代码
- 推送远端分支
- 通过 `github_issue_feedback` skill 回帖 issue

### 5. 任务可追踪性
task 需要稳定保留 GitHub issue 关联信息，至少确保：
- `github_issue_number`
- issue URL / repo 信息进入 task 描述或 stage prompt
- task 完成后可拼出 `http://127.0.0.1:3000/tasks/<task_id>` 形式的任务地址

## 关键设计
### 1. 模板定义
`github_issue_template` 使用标准 `StageDefinition` 字段：
- `name`: `dispatch_issue`
- `agent_role`: `issue distribution agent`
- `order`: `0`
- `instruction`: 强调读取 GitHub issue 上下文并输出结构化分发结果

- `name`: `process_security_issue`
- `agent_role`: `安全加密agent`
- `order`: `1`
- `instruction`: 强调读取 dispatch 输出，仅处理安全加密任务，并回帖 issue

### 2. Trigger 规则
GitHub webhook 命中 `github_issue_template` 后：
- 统一创建 task
- 标题使用 issue 标题渲染
- 描述中保留 issue URL、repo、作者、正文
- 真实 webhook 路径与 mock webhook 路径都要写入 `github_issue_number`

### 3. Distribution 行为
`issue distribution agent` 的职责是识别 issue 意图，不直接改代码。当前支持规则：
- 命中 “安全加密 / encryption / phone 字段加密” 等意图 -> `安全加密agent`
- 其他意图 -> 输出 unsupported 说明，供后续扩 worker agent 时接入

### 4. Worker 行为
`安全加密agent` 的职责是执行，不重新决定路由。它接收的上下文应包含：
- 原始 issue 标题、正文、URL、编号
- repo 信息
- distribution 产出的结构化工作单
- 当前 task_id 与 task_url

执行完成后必须回帖：
- Git 分支名
- Silicon Agent task URL

### 5. Skill 目录与权限
当前 worker 的技能分布在两个位置：
- 仓库根目录 `skills/des_encrypt/SKILL.md`
- 平台共享技能 `platform/skills/shared/github_issue_feedback/SKILL.md`

因此需要让角色配置与 skill dir 白名单对齐，确保：
- `issue distribution agent` 能加载 dispatch skill
- `安全加密agent` 能同时加载 `des_encrypt` 与 `github_issue_feedback`

### 6. 非目标
本轮不做以下内容：
- 不引入动态 stage 增删
- 不实现多个 worker agent 的真正分流执行
- 不要求 issue 回帖后自动关单
- 不在本轮内重构整套 worker 架构

## 失败处理
- 若 distribution 无法识别意图，task 保持失败或输出明确阻塞原因，不允许假成功
- 若 worker 未推送成功，不允许回帖成功态
- 若代码已推送但回帖失败，task 应能从错误日志定位到 comment API 失败原因

## 测试策略
### 1. 单元 / 服务层
- 内置模板 seed 正确创建
- 内置 agent seed 正确创建
- 真实 webhook 创建 task 时能保存 `github_issue_number`
- task 描述中包含 issue 关键上下文

### 2. mock webhook
- 构造 GitHub issue payload，命中 `github_issue_template`
- 验证创建出的 task stage 顺序与 agent_role 正确

### 3. 真实 issue 样本
- 使用 GHE API 拉取 `china/starbucks-asg-api#13`
- 以真实 title/body 验证 distribution 识别结果应为 `安全加密agent`

### 4. 闭环验证
若当前环境具备仓库访问和推送条件，则继续验证：
- task 被创建
- worker 进入安全加密 stage
- 分支被记录
- issue 评论包含 task URL 和 branch

## 交付结果
本次设计通过后，实施阶段需要至少交付：
- 一套稳定的 `github_issue_template`
- 一套稳定的 distribution / security worker 协议
- 一条可用的 GitHub issue 自动触发链路
- 针对 issue #13 的验证与修复结果
