# feature-009-GitHubIssue任务分发工作流 - 实现细节

## 1. 总体流程
1. GitHub issue webhook 进入 `/webhooks/github/{project_id}`。
2. `github.py` 将 issue payload 标准化，包含：
   - `issue_number`
   - `issue_url`
   - `issue_title`
   - `issue_body`
   - `repo_full_name`
3. `TriggerService.process_event(...)` 匹配到 `github_issue_template` 规则。
4. `TaskService.create_task(...)` 创建 task，并回填 `github_issue_number`。
5. 模板自动生成两个 stages：
   - `dispatch_issue`
   - `process_security_issue`
6. worker 执行 `dispatch_issue`，输出结构化分发结果。
7. worker 执行 `process_security_issue`，读取 dispatch 结果后按 `des_encrypt` skill 改代码、推分支、回帖 issue。

## 2. 关键改动点

### 2.1 模板 seed
在 `app/services/template_service.py` 中新增内置模板：
- `name = "github_issue_template"`
- 固定两阶段
- 不引入 gate

### 2.2 Agent seed / 配置
在 agent seed 路径补齐两个角色：
- `issue distribution agent`
- `安全加密agent`

要求：
- distribution agent 能加载 shared dispatch skill
- 安全加密 agent 能加载 shared feedback skill 与仓库级 `des_encrypt`
- 安全加密 agent 拥有执行 git / curl / 改代码所需工具权限

### 2.3 webhook 元数据传播
需要统一真实 webhook 与 mock webhook 的 issue 信息：
- `github_issue_number`
- issue URL
- repo_full_name
- issue body

其中 `github_issue_number` 不能只在 mock 流程补写，真实 webhook 创建 task 时也必须持久化。

### 2.4 prompt 合约
`app/worker/prompts.py` 中需要把以下语义写死：

#### distribution stage
- 只能分析和分发，不直接编码
- 必须输出结构化结果
- 当前若识别为安全加密类问题，必须选择 `安全加密agent`

#### security worker stage
- 严格依据 `des_encrypt` skill 执行
- 完成后推送远端分支
- 使用 `github_issue_feedback` skill 回帖 issue
- 回帖内容至少包含分支名和 task URL

## 3. issue #13 的预期识别结果
对于以下输入：
- title: `安全加密`
- body: `安全加密agent，对本项目的phone字段进行安全加密`

distribution 预期输出：
- `selected_agent_role = "安全加密agent"`
- `intent = "security_encryption"`
- `work_summary = "对 phone 字段进行安全加密改造"`

## 4. 测试实施方式

### 4.1 Template / seed
- 验证 `github_issue_template` 被 seed
- 验证 stages 顺序与 role 正确

### 4.2 webhook / task
- 验证真实 webhook 创建的 task 含 `github_issue_number`
- 验证 description 中保留 issue URL 与 repo 信息

### 4.3 prompt / contract
- 验证 distribution prompt 明确要求结构化 dispatch 输出
- 验证 security worker prompt 明确要求回帖 GitHub issue

### 4.4 真实样本
- 用 issue #13 的真实 title/body 做一个回归测试样本

## 5. 风险与边界
1. 当前 worker graph 仍是固定 stage 流程，不支持真正动态分配 stage。
2. `des_encrypt` skill 位于仓库根目录，需确保角色可见性和路径白名单一致。
3. GHE 评论回帖依赖 `GHE_TOKEN` 可用性与 repo 权限。
4. 若远端仓库无法推送，闭环验证只能做到“task 创建 + prompt 合同 + mock 验证”。

## 6. 回滚策略
若上线后发现新模板影响现有 webhook：
1. 禁用对应 trigger rule
2. 保留模板但从 UI 或 seed 中移除默认规则绑定
3. 不影响其他已有模板与非 GitHub 触发器
