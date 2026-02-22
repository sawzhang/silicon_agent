# SITC SkillKit 框架适配分析与 Agent 管理平台设计方案 v1.0

> **版本**: 1.0
> **日期**: 2026-02-21
> **团队**: SITC Trading Team
> **定位**: 内部技术方案 — 面向架构师/TL 的框架选型与平台设计文档
> **前置文档**: 《SITC数字员工集群v1.0设计方案》、《Silicon Agent战略演进白皮书》、《技术落地方案》

---

## 一、执行摘要

### 结论

**SkillKit 可以作为 SITC 数字员工 Agent 的运行时内核**。

经过对 `agent-skills-engine` 框架的逐模块分析，我们得出以下评估：

| 维度 | 评估 |
|------|------|
| **直接复用** | ~70% — AgentRunner运行时循环、SkillsEngine技能引擎、EventBus生命周期Hook、多LLM适配、Session持久化、Per-skill模型切换、Context Fork隔离执行、Hot-reload热更新 |
| **需要扩展** | ~30% — 多Agent编排层、Agent间通信协议、GitLab/Jira集成、Docker沙箱运行时、KPI采集上报、人工审批门控 |
| **需要新建** | Agent管理平台（Dashboard + Skills管理 + KPI监控 + 审批中心） |

### 核心判断

1. **单Agent能力充分**：SkillKit的 `AgentRunner` 已具备完整的Agent运行时循环——LLM调用、工具执行、技能注入、流式输出、中断恢复。每个数字员工角色可映射为一个独立的AgentRunner实例。
2. **多Agent编排是最大Gap**：SkillKit当前是"单Agent框架"范式，缺少跨Agent任务编排和通信机制。这是构建7角色集群最需要补齐的能力。
3. **管理平台需独立建设**：SkillKit的Web UI面向开发者交互，不适合作为运营Dashboard。需要独立建设面向架构师/TL的Agent集群管控中心。

---

## 二、SkillKit 框架能力全景

### 2.1 架构概览

```
skillkit/
├── agent.py              # AgentRunner — Agent运行时循环（2195行，核心）
├── engine.py             # SkillsEngine — 技能加载/过滤/快照
├── events.py             # EventBus — 生命周期事件总线
├── context.py            # ContextManager — 上下文窗口管理与压缩
├── config.py             # AgentConfig / SkillsConfig — 配置模型
├── models.py             # Skill / SkillSnapshot — 数据模型
├── model_registry.py     # ModelRegistry — 模型元数据与成本追踪
├── adapters/
│   ├── base.py           # LLMAdapter ABC — 多模型抽象接口
│   ├── openai.py         # OpenAI适配器（GPT-4o/o1等）
│   ├── anthropic.py      # Anthropic适配器（Claude系列）
│   └── registry.py       # AdapterRegistry — 适配器注册中心
├── runtime/
│   ├── base.py           # SkillRuntime ABC — 运行时抽象
│   └── bash.py           # BashRuntime — Shell命令执行
├── loaders/
│   └── markdown.py       # MarkdownSkillLoader — YAML+Markdown技能解析
├── session/
│   ├── manager.py        # SessionManager — 会话持久化（JSONL树结构）
│   └── tree.py           # 会话分支与遍历
├── tools/                # 原语工具：bash/read/write/edit/grep/find/ls
├── extensions/           # 扩展系统：插件发现/加载/API
├── web/                  # Web UI：Starlette + SSE/WebSocket
└── tui/                  # 终端UI：ANSI渲染
```

### 2.2 核心模块能力矩阵

| 模块 | SkillKit类/文件 | 核心能力 | 数字员工需求映射 | 适配度 |
|------|-----------------|---------|-----------------|--------|
| **AgentRunner** | `agent.py:AgentRunner` | Agent运行时循环：LLM调用→工具执行→结果处理→循环 | 每个角色一个Runner实例 | 直接复用 |
| **SkillsEngine** | `engine.py:SkillsEngine` | 技能加载/过滤/快照/热更新 | 三层Skills体系（L1/L2/L3）注入 | 直接复用 |
| **EventBus** | `events.py:EventBus` | 12种生命周期事件Hook | Agent间协调、KPI采集 | 需扩展 |
| **LLMAdapter** | `adapters/base.py:LLMAdapter` | 多模型适配（OpenAI/Anthropic） | 不同角色用不同模型 | 直接复用 |
| **Session** | `session/manager.py:SessionManager` | JSONL树结构会话持久化 | Agent执行记录追溯 | 直接复用 |
| **Per-skill model** | `agent.py:switch_model()` | 技能级模型切换 | 复杂任务用强模型 | 直接复用 |
| **Context Fork** | `agent.py:_execute_skill_forked()` | 创建子AgentRunner隔离执行 | Agent间互不干扰 | 直接复用 |
| **Hot-reload** | `engine.py` + `watchfiles` | 文件监听→快照失效→重新加载 | 运行中注入新知识 | 直接复用 |
| **BashRuntime** | `runtime/bash.py:BashRuntime` | Shell命令执行（流式输出/中断/超时） | git/编译/测试命令执行 | 需扩展 |
| **ContextManager** | `context.py:ContextManager` | Token预算/滑动窗口/上下文压缩 | 长任务上下文管理 | 直接复用 |
| **Extension** | `extensions/manager.py:ExtensionManager` | 插件发现/加载/API注入 | 自定义工具/命令扩展 | 直接复用 |

### 2.3 AgentRunner 运行时循环详解

`AgentRunner.chat()` 是整个框架的核心方法，其执行流程直接对应每个数字员工角色的工作循环：

```
用户输入（Jira Issue / 上游Agent输出）
    │
    ▼
┌─ INPUT事件 ─── 可拦截/转换输入
│
├─ Skill调度检查（/skill-name 触发）
│   ├─ $ARGUMENTS 替换
│   ├─ !`command` 动态内容注入
│   └─ context: fork → 创建子AgentRunner
│
├─ AGENT_START事件
│
├─ 循环（最多 max_turns 轮）
│   ├─ 检查中断信号（abort_signal）
│   ├─ 检查转向队列（steer）
│   ├─ TURN_START事件
│   ├─ CONTEXT_TRANSFORM事件 ─── 可裁剪/注入消息
│   ├─ 上下文压缩检查（should_compact）
│   ├─ 调用LLM（_call_llm）
│   ├─ TURN_END事件
│   │
│   ├─ 无工具调用 → 返回响应
│   │
│   └─ 有工具调用 → 逐个执行
│       ├─ BEFORE_TOOL_CALL事件 ─── 可拦截/修改
│       ├─ 执行工具（_execute_tool）
│       └─ AFTER_TOOL_RESULT事件 ─── 可修改结果
│
└─ AGENT_END事件
```

**关键设计点**：
- `steer(message)` — 允许外部在Agent执行过程中注入指令（可用于Orchestrator向下游Agent发送中间指令）
- `abort()` / `reset_abort()` — 支持优雅中断（可用于人工审批门控时暂停Agent）
- `BEFORE_TOOL_CALL` 事件返回 `ToolCallEventResult(block=True)` — 可阻止危险操作（天然的安全门控）

### 2.4 SkillsEngine 技能系统详解

#### 技能定义格式

每个Skill以 Markdown + YAML frontmatter 定义：

```markdown
---
name: code-generator
description: "根据Implementation Plan生成业务代码"
metadata:
  emoji: "💻"
  always: false
  requires:
    bins: ["git", "mvn"]
    env: ["GITLAB_TOKEN"]
  tags: ["coding", "L2"]
  version: "1.2.0"
model: "claude-opus-4"       # Per-skill模型覆盖
context: "fork"              # 隔离执行
allowed-tools: ["execute", "write", "read"]  # 工具白名单
user-invocable: true
---

# 代码生成技能

根据审批通过的Implementation Plan，在指定模块内生成业务代码。

## 约束
- 严格在Plan scope内编码，禁止越界修改
- 遵循 L1-coding-standards Skill 定义的命名规范
- 所有API必须符合OpenAPI Contract

## 流程
1. 读取 $1 指定的Plan文件
2. 解析变更范围（affected files）
3. 逐文件生成/修改代码
4. 运行本地编译验证
```

#### 技能过滤与快照

`SkillsEngine.get_snapshot()` 返回 `SkillSnapshot`，包含：
- 当前可用技能列表（经 `DefaultSkillFilter` 过滤）
- 格式化的Prompt文本（XML/Markdown/JSON）
- 版本号（用于缓存失效判断）

过滤条件通过 `metadata.requires` 声明：
- `bins` — 所有指定二进制必须存在
- `any_bins` — 至少一个二进制存在
- `env` — 所有指定环境变量必须设置
- `os` — 当前操作系统在支持列表中

#### 渐进式加载（Progressive Disclosure）

技能内容不直接注入System Prompt，而是分阶段加载：

1. **System Prompt阶段**：仅注入技能名称+描述（轻量索引）
2. **Skill Tool调用阶段**：LLM决定使用某技能时，加载完整内容
3. **参数替换阶段**：`$ARGUMENTS` / `$N` / `!`command`` 动态替换

这一机制天然适合三层Skills体系：L1常驻索引、L2按需加载、L3动态注入。

### 2.5 EventBus 事件系统详解

`EventBus` 提供12种生命周期事件，支持同步/异步Handler、优先级排序、来源追踪：

| 事件 | 触发时机 | 数据类型 | 可用于 |
|------|---------|---------|--------|
| `INPUT` | 用户输入接收 | `InputEvent(user_input)` | 输入预处理/路由 |
| `AGENT_START` | 首次LLM调用前 | `AgentStartEvent(user_input, model, turn)` | 初始化监控 |
| `TURN_START` | 每轮LLM调用前 | `TurnStartEvent(turn, message_count)` | Token预算检查 |
| `CONTEXT_TRANSFORM` | 消息发送LLM前 | `ContextTransformEvent(messages, turn)` | 上下文裁剪/注入 |
| `TURN_END` | 每轮LLM调用后 | `TurnEndEvent(turn, has_tool_calls, content)` | 输出监控 |
| `BEFORE_TOOL_CALL` | 工具执行前 | `BeforeToolCallEvent(tool_name, args, turn)` | **安全门控/审批拦截** |
| `TOOL_EXECUTION_UPDATE` | 工具执行中 | `ToolExecutionUpdateEvent(output, turn)` | 流式输出 |
| `AFTER_TOOL_RESULT` | 工具执行后 | `AfterToolResultEvent(tool_name, result, turn)` | **KPI数据采集** |
| `AGENT_END` | Agent循环结束 | `AgentEndEvent(total_turns, finish_reason)` | 任务完成通知 |
| `SESSION_START` | 会话启动 | `SessionStartEvent(session_id, resumed)` | 会话跟踪 |
| `SESSION_END` | 会话结束 | `SessionEndEvent(session_id, entry_count)` | 会话归档 |
| `MODEL_CHANGE` | 模型切换 | `ModelChangeEvent(previous, new)` | 成本追踪 |

**关键能力**：Handler可返回结构化结果，影响Agent行为：
- `BEFORE_TOOL_CALL` → 返回 `ToolCallEventResult(block=True, reason="...")` 阻止执行
- `AFTER_TOOL_RESULT` → 返回 `ToolResultEventResult(modified_result="...")` 修改结果
- `CONTEXT_TRANSFORM` → 返回 `ContextTransformEventResult(messages=[...])` 裁剪上下文
- `INPUT` → 返回 `InputEventResult(action="handle", response="...")` 直接响应

---

## 三、适配性深度分析：7角色 × SkillKit 映射

### 3.1 Orchestrator Agent（编排官）

| 维度 | 分析 |
|------|------|
| **SkillKit映射** | 一个专属 `AgentRunner` 实例 + Orchestration Skills |
| **模型选择** | Claude Opus 4 — 需要强推理能力进行任务分解 |
| **适配度** | ★★★☆☆ — 需要最多扩展 |

**核心Skills设计**：

```yaml
# L2 Skill: task-parser
---
name: task-parser
description: "解析Jira Issue为结构化任务DAG"
metadata:
  tags: ["orchestration", "L2"]
  requires:
    env: ["JIRA_TOKEN"]
model: "claude-opus-4"
---
解析Jira Issue的描述、Acceptance Criteria、附件，
生成结构化任务DAG（JSON格式），包含：
- 子任务列表（spec/code/test/review/smoke/doc）
- 依赖关系（DAG边）
- 每个子任务的Agent角色分配
- 预估Token预算
```

```yaml
# L2 Skill: task-router
---
name: task-router
description: "将子任务分发到对应角色的Agent"
metadata:
  tags: ["orchestration", "L2"]
---
根据任务DAG，将子任务按依赖顺序分发到对应Agent：
1. 检查目标Agent的健康状态
2. 构造标准任务Payload（task_id/agent_role/input/context）
3. 通过WorkforceOrchestrator发送任务
4. 监听Agent完成事件，触发下游任务
```

**需要扩展的能力**：
- 跨Agent任务传递机制（当前SkillKit是单Agent范式，`AgentRunner`之间无通信）
- DAG执行引擎（管理任务依赖、并行执行、失败重试）
- 全局任务状态存储（跨Agent共享的任务进度）

### 3.2 Spec Agent（方案官）

| 维度 | 分析 |
|------|------|
| **SkillKit映射** | `AgentRunner` + 方案设计Skills + `context: fork` 隔离执行 |
| **模型选择** | Claude Opus 4 — 需要强架构设计能力 |
| **适配度** | ★★★★☆ — 高度适配 |

**核心Skills设计**：

```yaml
# L2 Skill: impl-plan-generator
---
name: impl-plan-generator
description: "生成Implementation Plan（技术实施方案）"
context: fork
allowed-tools: ["read", "execute"]
model: "claude-opus-4"
metadata:
  tags: ["spec", "L2"]
---
基于Jira Issue需求和现有代码库，生成Implementation Plan：
1. 读取相关模块代码，理解现有架构
2. 执行 git log 了解最近变更历史
3. 设计变更方案：affected files、新增接口、数据库变更
4. 输出标准Plan文档（Markdown格式）
5. 标注与OpenAPI Contract的对齐点
```

**天然适配点**：
- `context: fork` 提供方案设计的隔离环境 — Spec Agent在独立子AgentRunner中工作，不污染主上下文
- `allowed-tools: ["read", "execute"]` 限制Spec Agent只能读代码和执行查询命令，不能写代码（职责隔离）
- `model: "claude-opus-4"` 方案设计使用最强模型，其他角色可用性价比更高的模型

### 3.3 Coding Agent（研发官）

| 维度 | 分析 |
|------|------|
| **SkillKit映射** | `AgentRunner` + 编码Skills + bash/read/write/edit全工具 |
| **模型选择** | Claude Sonnet 4（日常）/ Opus 4（复杂模块） — Per-skill切换 |
| **适配度** | ★★★★★ — 完美适配 |

**核心Skills设计**：

```yaml
# L2 Skill: code-generator（主编码技能）
---
name: code-generator
description: "根据Implementation Plan生成业务代码"
metadata:
  tags: ["coding", "L2"]
  requires:
    bins: ["git", "mvn"]
model: "claude-sonnet-4"
---
严格在Implementation Plan scope内编码：
1. 读取Plan文件，解析affected files列表
2. 逐文件生成/修改代码
3. 遵循L1 coding-standards（命名/复杂度/注释）
4. 确保与OpenAPI Contract完全对齐
5. 本地编译验证（mvn compile）
```

```yaml
# L1 Skill: scope-guard（越界防护）
---
name: scope-guard
description: "防止Coding Agent修改Plan范围外的文件"
metadata:
  tags: ["coding", "safety", "L1"]
  always: true
---
## 硬约束
- 仅允许修改Implementation Plan中列出的affected files
- 检测到越界修改时立即停止并上报
- 禁止修改main/release分支，仅操作feature branch
```

**天然适配点**：
- bash原语直接支持：`git checkout -b`、`mvn compile`、`npm run build`
- write/edit工具直接支持代码生成和修改
- read工具读取Implementation Plan和现有代码
- `BEFORE_TOOL_CALL` 事件可拦截越界写操作（scope-guard的技术实现）

### 3.4 Test Agent（测试官）

| 维度 | 分析 |
|------|------|
| **SkillKit映射** | `AgentRunner` + 测试Skills + bash(执行测试)/read(分析报告)/grep(搜索错误) |
| **模型选择** | Claude Sonnet 4 — 测试生成不需要最强推理 |
| **适配度** | ★★★★★ — 完美适配 |

**核心Skills设计**：

```yaml
# L2 Skill: unit-test-runner
---
name: unit-test-runner
description: "生成并执行单元测试，驱动自修复循环"
metadata:
  tags: ["testing", "L2"]
  requires:
    bins: ["mvn"]
---
1. 分析Coding Agent提交的代码变更
2. 基于变更生成单元测试（JUnit 5）
3. 执行测试：mvn test -pl $MODULE
4. 分析测试报告（surefire-reports XML）
5. 若失败：提取error log + stack trace，构造修复指令
6. 触发Self-Fix Loop（最多3轮）
```

```yaml
# L2 Skill: contract-test
---
name: contract-test
description: "验证代码实现与OpenAPI Contract的一致性"
metadata:
  tags: ["testing", "contract", "L2"]
  always: true
---
零容忍原则：任何Contract偏差 = 立即阻断
1. 读取OpenAPI YAML定义
2. 生成Contract测试（请求/响应Schema验证）
3. 执行验证，检测接口偏差
4. 偏差 > 0 → 标记为BLOCKER，停止后续流程
```

**天然适配点**：
- bash执行 `mvn test`、`npm test` 等测试命令
- read读取 surefire-reports XML、coverage报告
- grep搜索测试失败的 stack trace 和错误信息
- `AFTER_TOOL_RESULT` 事件可采集测试通过率、覆盖率等KPI

### 3.5 Review Agent（审计官）

| 维度 | 分析 |
|------|------|
| **SkillKit映射** | `AgentRunner` + 审计Skills + read(代码审查)/grep(模式匹配)/bash(安全扫描) |
| **模型选择** | Claude Opus 4 — 安全审计需要强推理 |
| **适配度** | ★★★★☆ — 高度适配 |

**核心Skills设计**：

```yaml
# L1 Skill: security-audit
---
name: security-audit
description: "OWASP Top 10安全审计"
metadata:
  tags: ["review", "security", "L1"]
  always: true
  requires:
    bins: ["gitleaks"]
---
三维审计之一 — 安全维度：
1. 执行 gitleaks detect 检测PII泄露
2. 审查SQL拼接、XSS风险、SSRF风险
3. 检查依赖项安全版本
4. 输出安全审计报告（严重/高/中/低）
```

```yaml
# L1 Skill: perf-audit
---
name: perf-audit
description: "性能审计：N+1查询/内存泄露/慢SQL"
metadata:
  tags: ["review", "performance", "L1"]
---
三维审计之二 — 性能维度：
1. 检测N+1查询模式（JPA/MyBatis）
2. 检查资源未关闭（Connection/Stream）
3. 分析循环内DB调用
4. 输出性能审计报告
```

**天然适配点**：
- read工具读取代码进行静态分析
- grep工具搜索危险模式（SQL拼接、eval、exec等）
- bash执行 `gitleaks detect`、`sonar-scanner` 等安全工具
- Skills以自然语言注入审计规则，LLM理解语义级安全风险

### 3.6 Smoke Agent（巡检官）

| 维度 | 分析 |
|------|------|
| **SkillKit映射** | `AgentRunner` + 巡检Skills + bash(执行E2E测试) |
| **模型选择** | Claude Sonnet 4 — E2E验证以执行为主 |
| **适配度** | ★★★☆☆ — 需要Docker沙箱扩展 |

**核心Skills设计**：

```yaml
# L2 Skill: e2e-validator
---
name: e2e-validator
description: "端到端集成验证（JD/Douyin/Mini-Program链路）"
metadata:
  tags: ["smoke", "e2e", "L2"]
  requires:
    bins: ["docker"]
---
1. 启动Mock Server（模拟JD/Douyin平台接口）
2. 部署被测服务到Docker容器
3. 执行E2E测试场景
4. 验证全链路数据流转
5. 输出巡检报告（通过/失败/异常）
```

**需要扩展的能力**：
- Docker沙箱环境集成：当前 `BashRuntime` 直接在主机执行，Smoke Agent需要在Docker容器内运行测试
- Mock Server管理：需要Skill能启动/停止模拟平台的Mock服务
- 网络隔离验证：确保Agent运行环境符合零信任网络策略

### 3.7 Doc Agent（文档官）

| 维度 | 分析 |
|------|------|
| **SkillKit映射** | `AgentRunner` + 文档Skills + write(生成文档)/bash(提交PR)/read(读取变更) |
| **模型选择** | Claude Sonnet 4 — 文档生成使用性价比模型 |
| **适配度** | ★★★★★ — 完美适配 |

**核心Skills设计**：

```yaml
# L2 Skill: changelog-generator
---
name: changelog-generator
description: "根据git diff和Jira Issue自动生成Changelog"
metadata:
  tags: ["doc", "L2"]
---
1. 读取本次迭代的git log和diff
2. 关联Jira Issue获取需求描述
3. 生成结构化Changelog（功能变更/Bug修复/破坏性变更）
4. 输出Markdown格式文档
```

```yaml
# L3 Skill: skill-extractor
---
name: skill-extractor
description: "从解决方案中提炼可复用Skill"
metadata:
  tags: ["doc", "knowledge", "L3"]
model: "claude-opus-4"
---
知识沉淀核心机制：
1. 分析本次任务的Problem → Solution路径
2. 识别可复用模式（边界条件处理/平台适配技巧/架构模式）
3. 提炼为标准Skill格式（YAML frontmatter + Markdown）
4. 提交Skill审批工作流
```

**天然适配点**：
- write工具直接生成Markdown文档
- bash执行 `git diff`、`git log`、`git commit`、`git push` 提交文档PR
- read读取代码变更和现有文档
- skill-extractor是Doc Agent最核心的价值——将项目经验沉淀为可复用Skills，实现知识的永久资产化

### 3.8 适配度总览

| 角色 | 适配度 | 直接复用 | 需扩展 | 核心SkillKit能力依赖 |
|------|--------|---------|--------|---------------------|
| Orchestrator | ★★★☆☆ | 40% | 60% | AgentRunner + EventBus + **新增编排层** |
| Spec Agent | ★★★★☆ | 85% | 15% | AgentRunner + Context Fork + read工具 |
| Coding Agent | ★★★★★ | 95% | 5% | AgentRunner + bash/write/edit + Per-skill model |
| Test Agent | ★★★★★ | 90% | 10% | AgentRunner + bash + grep + AFTER_TOOL_RESULT |
| Review Agent | ★★★★☆ | 85% | 15% | AgentRunner + read/grep + BEFORE_TOOL_CALL |
| Smoke Agent | ★★★☆☆ | 50% | 50% | AgentRunner + **Docker沙箱** + bash |
| Doc Agent | ★★★★★ | 95% | 5% | AgentRunner + write/bash + read |

---

## 四、Gap 分析：需要扩展的能力

### Gap 1：多Agent编排层（最关键）

**优先级**：P0 — 阻塞整个集群运行

**现状分析**：
- SkillKit的 `AgentRunner` 是单Agent运行时，每个实例独立运行自己的chat循环
- `Context Fork`（`_execute_skill_forked()`）可以创建子AgentRunner，但这是"父→子"的调用关系，不是"平级协作"
- `EventBus` 是进程内事件，无法跨AgentRunner实例通信

**目标能力**：
- Orchestrator能够创建、调度、监控7个Agent实例
- 实现任务DAG执行引擎（依赖管理/并行执行/失败处理）
- 跨Agent结构化任务传递

**扩展方案 — 新增 `WorkforceOrchestrator` 类**：

```python
# 新增模块：skillkit/workforce/orchestrator.py

class WorkforceOrchestrator:
    """管理7个AgentRunner实例的编排层"""

    def __init__(self, config: WorkforceConfig):
        self.agents: Dict[AgentRole, AgentRunner] = {}
        self.task_dag: TaskDAG = TaskDAG()
        self.message_bus: MessageBus = MessageBus()  # 跨Agent通信
        self.human_gates: Dict[str, HumanGate] = {}  # 人工审批门控

    async def register_agent(self, role: AgentRole, runner: AgentRunner):
        """注册一个角色Agent"""
        self.agents[role] = runner
        # 在Agent的EventBus上注册跨Agent事件路由
        runner.events.on("agent_end", self._on_agent_complete)
        runner.events.on("after_tool_result", self._on_tool_result)

    async def dispatch_task(self, task: Task):
        """解析Jira Issue为DAG并开始执行"""
        dag = await self._parse_task_to_dag(task)
        await self._execute_dag(dag)

    async def _execute_dag(self, dag: TaskDAG):
        """DAG执行引擎：拓扑排序→并行执行就绪节点"""
        while not dag.is_complete():
            ready_tasks = dag.get_ready_tasks()  # 无前置依赖的任务
            results = await asyncio.gather(*[
                self._run_agent_task(task) for task in ready_tasks
            ])
            for task, result in zip(ready_tasks, results):
                dag.mark_complete(task.id, result)

    async def _run_agent_task(self, task: AgentTask):
        """在对应角色的AgentRunner上执行任务"""
        agent = self.agents[task.agent_role]

        # 检查人工审批门控
        if task.requires_human_gate:
            await self._wait_for_human_approval(task)

        # 构造任务输入
        task_input = self._format_task_input(task)

        # 调用Agent的chat方法
        result = await agent.chat(task_input)

        # 通过MessageBus广播完成事件
        await self.message_bus.publish(AgentTaskComplete(
            task_id=task.id,
            agent_role=task.agent_role,
            result=result
        ))

        return result
```

**实现要点**：
- `WorkforceOrchestrator` 不修改 `AgentRunner` 内部逻辑，而是在外层编排
- 复用 `EventBus` 的事件订阅机制进行Agent状态监听
- 任务DAG通过拓扑排序确定执行顺序，就绪节点并行执行

---

### Gap 2：Agent间通信协议

**优先级**：P0 — 与Gap 1配套

**现状分析**：
- `EventBus.emit()` 是 `async` 方法，但作用域限于单个 `AgentRunner` 实例
- `EventBus.on()` 注册的Handler与特定Bus实例绑定

**目标能力**：
- 结构化JSON任务传递（agent_role/task_id/payload/status）
- Orchestrator → 下游Agent的任务下发
- 下游Agent → Orchestrator的结果回传
- Agent间的中间产物传递（如Spec的Plan → Coding的输入）

**扩展方案 — 新增 `MessageBus` 类**：

```python
# 新增模块：skillkit/workforce/message_bus.py

@dataclass
class AgentMessage:
    """跨Agent通信的标准消息格式"""
    task_id: str
    from_role: AgentRole
    to_role: AgentRole
    payload_type: str  # "task_input" | "task_result" | "intermediate" | "error"
    payload: dict
    timestamp: datetime
    metadata: dict = field(default_factory=dict)

class MessageBus:
    """跨Agent通信总线"""

    # Phase 1：进程内实现（asyncio.Queue）
    # Phase 2：可替换为Redis Pub/Sub实现分布式部署

    def __init__(self, backend: str = "memory"):
        if backend == "memory":
            self._queues: Dict[AgentRole, asyncio.Queue] = {}
        elif backend == "redis":
            self._redis = aioredis.from_url(...)

    async def publish(self, message: AgentMessage):
        """发布消息到目标Agent的队列"""
        queue = self._queues[message.to_role]
        await queue.put(message)

    async def subscribe(self, role: AgentRole) -> AsyncIterator[AgentMessage]:
        """订阅指定角色的消息队列"""
        queue = self._queues[role]
        while True:
            message = await queue.get()
            yield message
```

**设计原则**：
- Phase 1使用 `asyncio.Queue`（进程内），满足单机部署
- Phase 2可替换为 Redis Pub/Sub，满足分布式部署
- 消息格式标准化，与SkillKit的 `AgentMessage` 区分（命名为 `WorkforceMessage`）

---

### Gap 3：GitLab/Jira 集成

**优先级**：P1 — Phase 2需要

**现状分析**：
- SkillKit有GitHub相关的Skills（如bundled skills），但无GitLab/Jira专用集成
- bash原语可以执行 `git` 命令（push/pull/checkout），但缺少GitLab API和Jira API的封装

**目标能力**：
- Jira Webhook → 自动触发Agent集群处理
- GitLab MR 创建/评审/合并的API调用
- Jira Issue状态自动更新

**扩展方案 — 新增集成Skills + Webhook服务**：

```yaml
# 新增Skill: gitlab-mr
---
name: gitlab-mr
description: "创建/更新/合并GitLab Merge Request"
metadata:
  tags: ["integration", "gitlab", "L1"]
  requires:
    env: ["GITLAB_TOKEN", "GITLAB_URL"]
---
GitLab MR操作封装：
1. 创建MR：指定source/target branch、title、description
2. 添加Review标签
3. 查询MR状态和评审意见
4. 合并MR（需Review Agent审批通过）

使用方式：
- 创建MR：通过bash执行 curl 调用GitLab API
- API Base: ${GITLAB_URL}/api/v4/projects/{project_id}/merge_requests
```

```yaml
# 新增Skill: jira-sync
---
name: jira-sync
description: "同步Jira Issue状态和工作日志"
metadata:
  tags: ["integration", "jira", "L1"]
  requires:
    env: ["JIRA_TOKEN", "JIRA_URL"]
---
Jira状态自动同步：
1. 读取Issue详情（描述、AC、附件）
2. 更新Issue状态（To Do → In Progress → Done）
3. 添加工作日志（Agent处理记录）
4. 添加Comment（Agent产出物链接）
```

**Webhook接收层**（独立于SkillKit，属于管理平台功能）：

```python
# 管理平台模块：platform/webhooks/jira.py

@app.post("/webhooks/jira")
async def handle_jira_webhook(payload: JiraWebhookPayload):
    """接收Jira Webhook，触发Agent集群"""
    if payload.event == "issue_created" and "agent-task" in payload.labels:
        task = Task(
            jira_id=payload.issue_key,
            title=payload.summary,
            description=payload.description,
            acceptance_criteria=payload.custom_fields.get("ac"),
        )
        await orchestrator.dispatch_task(task)
```

---

### Gap 4：Docker 沙箱运行时

**优先级**：P1 — Smoke Agent和安全隔离依赖

**现状分析**：
- `BashRuntime`（`runtime/bash.py`）通过 `asyncio.create_subprocess_shell()` 直接在主机执行命令
- 无容器隔离，Agent的bash命令可访问主机文件系统和网络

**目标能力**：
- Agent的bash命令在Docker容器内执行
- 容器使用 `Corporate_Std_v4.2` 基础镜像（JDK 17 + Maven + Node.js）
- 网络隔离（仅允许白名单出站）
- 文件系统隔离（只挂载项目代码目录）

**扩展方案 — 新增 `DockerBashRuntime`**：

```python
# 新增模块：skillkit/runtime/docker.py

class DockerBashRuntime(SkillRuntime):
    """在Docker容器内执行Agent的bash命令"""

    def __init__(self, config: DockerRuntimeConfig):
        self.image = config.image  # "corporate-std:v4.2"
        self.network = config.network  # "agent-sandbox-net"（白名单出站）
        self.volumes = config.volumes  # {"/code/project": "/workspace"}
        self.resource_limits = config.limits  # CPU/Memory limits
        self._container = None

    async def execute(self, command, cwd=None, env=None,
                      timeout=30, on_output=None, abort_signal=None):
        """在Docker容器内执行命令"""
        container = await self._ensure_container()
        exec_result = await container.exec_run(
            cmd=["bash", "-c", command],
            workdir=cwd or "/workspace",
            environment=env,
            stream=True
        )
        return await self._collect_output(
            exec_result, timeout, on_output, abort_signal
        )

    async def _ensure_container(self):
        """懒创建容器，复用已有容器"""
        if self._container is None:
            self._container = await self._create_container()
        return self._container
```

**实现要点**：
- `DockerBashRuntime` 实现 `SkillRuntime` 抽象接口，与 `BashRuntime` 接口完全一致
- 通过 `AgentConfig` 配置选择使用哪种Runtime
- 容器在Agent首次执行命令时懒创建，复用直到Agent任务完成
- 支持 `on_output` 流式回调和 `abort_signal` 中断

---

### Gap 5：KPI 采集与上报

**优先级**：P1 — Dashboard和决策依赖

**现状分析**：
- `EventBus` 已提供 `AFTER_TOOL_RESULT` 和 `AGENT_END` 等事件Hook
- `ModelRegistry.TokenUsage` 已追踪Token消耗（input/output/cache/thinking）
- 但无指标采集、聚合、上报的完整链路

**目标能力**：
- 采集12项KPI指标（FPR、自修复率、覆盖率、Human-Touch等）
- Prometheus指标暴露
- 实时Dashboard展示

**扩展方案 — 基于EventBus的KPI采集Extension**：

```python
# 新增Extension：skillkit/extensions/kpi_collector.py

class KPICollectorExtension:
    """通过EventBus采集Agent KPI指标"""

    def extension(self, api):
        # 注册事件监听
        api.on("after_tool_result", self._collect_tool_metrics)
        api.on("agent_end", self._collect_agent_metrics)
        api.on("model_change", self._collect_cost_metrics)

    async def _collect_tool_metrics(self, event: AfterToolResultEvent):
        """从工具执行结果中提取KPI"""
        if event.tool_name == "execute":
            # 分析bash命令结果
            if "mvn test" in str(event.args):
                # 解析测试报告，提取通过率和覆盖率
                coverage = self._parse_coverage(event.result)
                test_pass_rate = self._parse_test_results(event.result)
                self._emit_metric("test_coverage", coverage)
                self._emit_metric("test_pass_rate", test_pass_rate)

            if "gitleaks" in str(event.args):
                # 安全扫描结果
                security_issues = self._parse_security(event.result)
                self._emit_metric("security_issues", security_issues)

    async def _collect_agent_metrics(self, event: AgentEndEvent):
        """Agent任务完成时的综合指标"""
        self._emit_metric("agent_turns", event.total_turns)
        self._emit_metric("agent_finish_reason", event.finish_reason)

    def _emit_metric(self, name, value):
        """上报指标到Prometheus"""
        AGENT_METRICS.labels(
            agent_role=self.agent_role,
            metric_name=name
        ).set(value)
```

**KPI指标与EventBus事件的映射**：

| KPI指标 | 数据源 | EventBus事件 | 采集方式 |
|---------|--------|-------------|---------|
| Agent-FPR | Test Agent测试结果 | `AFTER_TOOL_RESULT`（mvn test） | 解析surefire-reports |
| Self-Fix Rate | 自修复循环轮次 | `AGENT_END`（total_turns） | turns ≤ 3 = 成功 |
| Plan Approval Rate | 人工审批结果 | `HumanGate` 回调 | 统计通过/拒绝 |
| Coverage Rate | 测试覆盖率报告 | `AFTER_TOOL_RESULT` | 解析JaCoCo报告 |
| Human-Touch Time | 人工介入时长 | `HumanGate` 暂停/恢复时间差 | 计时统计 |
| Token-vs-PSP Ratio | Token消耗 | `MODEL_CHANGE` + `TokenUsage` | 累计Token成本 |
| Review Accuracy | Review Agent误报率 | `AGENT_END`（Review角色） | 人工确认统计 |

---

### Gap 6：人工审批门控

**优先级**：P0 — "Human for Decision, Agent for Execution" 的核心机制

**现状分析**：
- `AgentRunner` 的chat循环是自动执行到完成的（`max_turns` 控制上限）
- `abort()` 方法可以中断执行，但是一次性中断，不支持"暂停→等待→恢复"
- `steer()` 方法可以注入中间指令，但不能暂停循环等待外部输入

**目标能力**：
- Spec方案审批（Spec Agent → 人工确认 → Coding Agent）
- Review确认（Review Agent → 人工确认 → 合并/打回）
- 最终签收（全流程完成 → 人工签收 → Jira关闭）

**扩展方案 — 新增 `HumanGate` 机制**：

```python
# 新增模块：skillkit/workforce/human_gate.py

class HumanGate:
    """人工审批门控：暂停Agent执行，等待人工决策"""

    def __init__(self, gate_id: str, gate_type: GateType):
        self.gate_id = gate_id
        self.gate_type = gate_type  # SPEC_APPROVAL | REVIEW_CONFIRM | FINAL_SIGNOFF
        self._approval_event = asyncio.Event()
        self._result: Optional[GateResult] = None
        self.created_at = datetime.now()

    async def wait_for_approval(self, timeout: int = 3600) -> GateResult:
        """阻塞等待人工审批（默认超时1小时）"""
        try:
            await asyncio.wait_for(
                self._approval_event.wait(),
                timeout=timeout
            )
            return self._result
        except asyncio.TimeoutError:
            return GateResult(approved=False, reason="审批超时")

    def approve(self, reviewer: str, comment: str = ""):
        """人工通过审批"""
        self._result = GateResult(
            approved=True, reviewer=reviewer, comment=comment,
            reviewed_at=datetime.now()
        )
        self._approval_event.set()

    def reject(self, reviewer: str, reason: str):
        """人工拒绝"""
        self._result = GateResult(
            approved=False, reviewer=reviewer, reason=reason,
            reviewed_at=datetime.now()
        )
        self._approval_event.set()

@dataclass
class GateResult:
    approved: bool
    reviewer: str = ""
    comment: str = ""
    reason: str = ""
    reviewed_at: datetime = None
```

**在WorkforceOrchestrator中的集成**：

```python
# orchestrator.py 中的使用

async def _run_agent_task(self, task: AgentTask):
    agent = self.agents[task.agent_role]
    result = await agent.chat(task_input)

    # 检查该任务是否需要人工审批
    if task.agent_role == AgentRole.SPEC:
        gate = HumanGate("spec-approval", GateType.SPEC_APPROVAL)
        self.human_gates[gate.gate_id] = gate

        # 通知管理平台有待审批项
        await self._notify_platform(PendingApproval(
            gate_id=gate.gate_id,
            task_id=task.id,
            content=result,  # Spec Agent的方案输出
            agent_role="spec"
        ))

        # 阻塞等待人工审批
        gate_result = await gate.wait_for_approval()

        if not gate_result.approved:
            # 审批拒绝 → 重新执行Spec Agent（附带拒绝原因）
            return await self._retry_with_feedback(task, gate_result.reason)

    return result
```

---

### Gap 汇总

| Gap | 优先级 | 工作量 | 依赖 | 涉及SkillKit模块 |
|-----|--------|--------|------|------------------|
| Gap 1: 多Agent编排层 | P0 | 2周 | 无 | 新增 `workforce/orchestrator.py` |
| Gap 2: Agent间通信协议 | P0 | 1周 | Gap 1 | 新增 `workforce/message_bus.py` |
| Gap 6: 人工审批门控 | P0 | 1周 | Gap 1 | 新增 `workforce/human_gate.py` |
| Gap 3: GitLab/Jira集成 | P1 | 1.5周 | 无 | 新增Skills + Webhook服务 |
| Gap 5: KPI采集上报 | P1 | 1周 | 无 | 新增Extension `kpi_collector.py` |
| Gap 4: Docker沙箱 | P1 | 1.5周 | 无 | 新增 `runtime/docker.py` |

---

## 五、扩展架构设计

### 5.1 整体架构

```
┌─────────────────────────────────────────────────────────────┐
│              Agent Management Platform (Web)                 │
│   集群总览 · 任务Pipeline · 审批中心 · Skills管理 · KPI监控    │
│   技术栈：React + Ant Design + FastAPI + PostgreSQL          │
├─────────────────────────────────────────────────────────────┤
│              Webhook Layer（新增）                             │
│   Jira Webhook Receiver · GitLab Webhook Receiver            │
├─────────────────────────────────────────────────────────────┤
│              WorkforceOrchestrator（新增）                     │
│   ┌──────────────────────────────────────────────────────┐  │
│   │  TaskDAG Engine    Agent Scheduler    HumanGate      │  │
│   │  任务DAG解析/执行   Agent实例调度     人工审批门控     │  │
│   └──────────────────────────────────────────────────────┘  │
│   ┌──────────────────────────────────────────────────────┐  │
│   │  MessageBus（Phase1: asyncio.Queue → Phase2: Redis） │  │
│   │  跨Agent结构化消息传递 · 任务结果路由                   │  │
│   └──────────────────────────────────────────────────────┘  │
├──────┬──────┬──────┬──────┬──────┬──────┬────────────────┤
│Orch  │Spec  │Code  │Test  │Review│Smoke │Doc             │
│Agent │Agent │Agent │Agent │Agent │Agent │Agent           │
│      │      │      │      │      │      │                │
│Opus4 │Opus4 │Son.4 │Son.4 │Opus4 │Son.4 │Son.4           │
├──────┴──────┴──────┴──────┴──────┴──────┴────────────────┤
│              SkillKit Core（完全复用）                       │
│   ┌────────────────────────────────────────────────────┐  │
│   │  AgentRunner        SkillsEngine      EventBus     │  │
│   │  Agent运行时循环    技能加载/过滤/快照  生命周期Hook  │  │
│   ├────────────────────────────────────────────────────┤  │
│   │  SessionManager     ContextManager    LLMAdapter   │  │
│   │  JSONL会话持久化    Token预算/压缩    多模型适配     │  │
│   ├────────────────────────────────────────────────────┤  │
│   │  ExtensionManager   ModelRegistry     SkillLoader  │  │
│   │  插件系统           模型元数据/成本    MD+YAML解析   │  │
│   └────────────────────────────────────────────────────┘  │
├─────────────────────────────────────────────────────────────┤
│              Skills Repository（Git管理）                     │
│   ┌─────────┐  ┌──────────────────┐  ┌─────────────────┐  │
│   │ L1通用   │  │ L2领域            │  │ L3迭代           │  │
│   │ 编码规范 │  │ 兑换/权益/组套    │  │ Sprint DoD      │  │
│   │ 安全审计 │  │ JD/Douyin适配    │  │ 接口变更记录     │  │
│   │ Git工作流│  │ 性能/契约测试    │  │ 技术决策记录     │  │
│   └─────────┘  └──────────────────┘  └─────────────────┘  │
├─────────────────────────────────────────────────────────────┤
│              Integration Layer（新增）                        │
│   Jira Adapter（REST API）· GitLab Adapter（REST API）       │
│   Slack Adapter（Webhook）· SonarQube Adapter                │
├─────────────────────────────────────────────────────────────┤
│              Infrastructure（新增/扩展）                       │
│   ┌──────────────┐  ┌────────┐  ┌────────────┐  ┌───────┐ │
│   │DockerBash    │  │Redis   │  │Prometheus  │  │Grafana│ │
│   │Runtime       │  │消息队列│  │KPI Exporter│  │可视化 │ │
│   │容器化执行    │  │Agent通信│  │指标采集    │  │仪表盘 │ │
│   └──────────────┘  └────────┘  └────────────┘  └───────┘ │
└─────────────────────────────────────────────────────────────┘
```

### 5.2 数据流

```
Jira Issue创建（带agent-task标签）
    │
    ▼
Webhook Receiver 接收事件
    │
    ▼
WorkforceOrchestrator.dispatch_task()
    │
    ▼
Orchestrator Agent（task-parser Skill）
    │ 解析为任务DAG
    ▼
┌───────────────────────────────────┐
│           TaskDAG Engine           │
│                                    │
│  ①Spec Agent → [人工审批门控]      │
│       │                            │
│  ②Coding Agent(×2并行)            │
│       │                            │
│  ③Test Agent → Self-Fix Loop(≤3)  │
│       │                            │
│  ④Review Agent → [人工确认门控]    │
│       │                            │
│  ⑤Smoke Agent → E2E验证           │
│       │                            │
│  ⑥Doc Agent → Changelog/Wiki      │
│       │                            │
│  ⑦[人工最终签收门控]               │
└───────────────────────────────────┘
    │
    ▼
Jira Issue → Done
GitLab MR → Merged
Slack → 完成通知
```

### 5.3 Skills 三层体系与 SkillKit 映射

SkillKit的 `SkillsEngine` 通过 `skill_dirs` 配置项支持多目录加载，天然映射三层Skills体系：

```python
# 三层Skills目录映射
skills_config = SkillsConfig(
    skill_dirs=[
        "/skills/L1-common",        # L1: 通用标准（编码规范/安全/Git）
        "/skills/L2-domain",        # L2: 领域知识（兑换/权益/平台适配）
        "/skills/L3-sprint",        # L3: 迭代上下文（当前Sprint）
    ],
    watch=True,                     # 热更新：L3变更即时生效
    watch_debounce_ms=250,
)
```

**加载策略**：
- `skill_dirs` 按顺序加载，**后加载的同名Skill覆盖先加载的**——L3可覆盖L2的特定Skill（当前Sprint有特殊处理逻辑时）
- `metadata.always: true` 的Skill始终加载到System Prompt（L1安全规范适用）
- `metadata.requires` 过滤当前环境不适用的Skill（如Windows环境过滤Linux-only的Skill）

**更新频率与SkillKit热更新的对应**：

| 层级 | 更新频率 | SkillKit机制 |
|------|---------|-------------|
| L1 通用标准 | 季度 | Skills文件更新 → `watchfiles` 检测 → 快照失效 → 重新加载 |
| L2 领域知识 | 月度 | 同上，但更新更频繁 |
| L3 迭代上下文 | 每Sprint | 热更新生效，当前运行中的Agent下一轮chat即可感知新Skills |

---

## 六、Agent 管理平台设计

### 6.1 平台定位

**面向用户**：架构师、TL、知识管理员、DevOps/安全团队

**核心价值**：让非AI专家能够管控Agent集群——启动/停止/配置Agent、审批人工卡点、监控KPI、管理Skills知识库

**非目标**：不是给最终用户（业务人员）使用的平台，不需要面向公众的UI

### 6.2 技术选型

| 层级 | 技术 | 选型理由 |
|------|------|---------|
| **前端** | React + Ant Design Pro | 中后台标准方案，组件丰富，团队熟悉 |
| **后端** | Python FastAPI | 与SkillKit同为Python生态，可直接import调用；原生async |
| **数据库** | PostgreSQL | 结构化数据（Agent状态/任务记录/KPI历史），JSONB支持灵活Schema |
| **缓存/队列** | Redis | Agent间通信（MessageBus Phase 2）、实时状态缓存、WebSocket pub/sub |
| **监控** | Prometheus + Grafana | KPI指标采集与可视化，业界标准方案 |
| **实时通信** | WebSocket（Starlette） | Agent状态实时推送到前端Dashboard |

### 6.3 核心功能模块

| 模块 | 核心功能 | 主要用户 | 数据源 |
|------|---------|---------|--------|
| **集群总览** | 7个Agent运行状态、实时活动、健康度仪表盘 | 架构师/TL | AgentRunner状态 + EventBus事件 |
| **任务管线** | Jira Issue全生命周期可视化：当前阶段、耗时、阻塞点 | 架构师 | TaskDAG + Jira API |
| **审批中心** | Spec方案审批、Review确认、最终签收（三个人工卡点） | 架构师 | HumanGate队列 |
| **Skills管理** | L1/L2/L3技能CRUD、版本历史、使用统计、审批工作流 | 知识管理员 | Git仓库 + SkillsEngine |
| **KPI Dashboard** | FPR/覆盖率/Human-Touch/Token成本等12项指标实时监控 | TL/管理层 | Prometheus + KPICollector |
| **审计日志** | Agent全操作日志查询、安全事件回溯 | DevOps/安全 | SessionManager日志 + EventBus |
| **Agent配置** | 每个角色的模型选择、Skills挂载、Prompt模板、超参数 | 架构师 | AgentConfig |
| **止损控制台** | 三级止损机制的触发/解除/回退操作 | 架构师/TL | 止损状态机 |

### 6.4 关键页面设计

#### 页面一：集群总览

```
┌──────────────────────────────────────────────────────────────────┐
│  SITC Digital Workforce Dashboard                    [配置] [帮助]│
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌─Agent集群状态──────────────────────────────────────────────┐  │
│  │                                                            │  │
│  │  ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐              │  │
│  │  │Orch    │ │Spec    │ │Code×2  │ │Test    │              │  │
│  │  │● 运行中│ │● 运行中│ │● 运行中│ │◐ 等待中│              │  │
│  │  │Task:3  │ │Plan:#42│ │PR:#108 │ │Queue:2 │              │  │
│  │  └────────┘ └────────┘ └────────┘ └────────┘              │  │
│  │  ┌────────┐ ┌────────┐ ┌────────┐                         │  │
│  │  │Review  │ │Smoke   │ │Doc     │                         │  │
│  │  │○ 空闲  │ │○ 空闲  │ │◐ 等待中│                         │  │
│  │  │Last:2h │ │Last:4h │ │Queue:1 │                         │  │
│  │  └────────┘ └────────┘ └────────┘                         │  │
│  │                                                            │  │
│  │  ● 运行中:3  ◐ 等待中:2  ○ 空闲:2  ✕ 故障:0               │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                  │
│  ┌─实时活动流────────────────────┐ ┌─今日KPI速览──────────────┐  │
│  │ 14:32 Code Agent #1 提交PR    │ │ FPR          78%  ↑ 5%  │  │
│  │ 14:30 Test Agent 测试通过 ✓   │ │ 覆盖率        82%  ↑ 2%  │  │
│  │ 14:28 Code Agent #1 修复Bug   │ │ Self-Fix     87%  →     │  │
│  │ 14:25 Test Agent 发现失败 ✗   │ │ Human-Touch  65min ↓10m │  │
│  │ 14:20 Spec Agent 方案已审批 ✓ │ │ Token成本    ¥128  →    │  │
│  │ 14:15 Orch Agent 分解任务 #42 │ │ 安全问题      0    ✓    │  │
│  └──────────────────────────────┘ └──────────────────────────┘  │
│                                                                  │
│  ┌─待处理审批──────────────────────────────────────────────────┐ │
│  │ ⚠ [Spec审批] ISSUE-42 兑换模块重构方案   等待12分钟 [审批]  │ │
│  │ ⚠ [Review]  PR-108 权益积分计算逻辑      等待5分钟  [确认]  │ │
│  └──────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
```

#### 页面二：任务Pipeline

```
┌──────────────────────────────────────────────────────────────────┐
│  任务管线  ISSUE-42: 兑换模块新增积分抵扣功能                      │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌─Pipeline视图────────────────────────────────────────────────┐ │
│  │                                                              │ │
│  │  [解析]──→[方案]──→[审批]──→[编码]──→[测试]──→[审查]──→[巡检]│ │
│  │   ✓       ✓       ✓      ●进行中   ○       ○       ○      │ │
│  │  3min    25min   12min   进行中    --      --      --     │ │
│  │                                                              │ │
│  │  总耗时: 52min (进行中)    预估剩余: ~45min                  │ │
│  └──────────────────────────────────────────────────────────────┘ │
│                                                                  │
│  ┌─当前阶段详情：编码───────────────────────────────────────────┐ │
│  │                                                              │ │
│  │  Agent: Coding Agent #1           模型: claude-sonnet-4      │ │
│  │  开始时间: 14:28                  当前Turn: 5/30             │ │
│  │  Token消耗: 12,450 (¥0.18)       Skills加载: 4个            │ │
│  │                                                              │ │
│  │  加载的Skills:                                               │ │
│  │  ├─ L1: coding-standards (v2.1)                              │ │
│  │  ├─ L1: scope-guard (v1.0)                                   │ │
│  │  ├─ L2: redemption-rules (v3.2)                              │ │
│  │  └─ L3: sprint-42-context (v1)                               │ │
│  │                                                              │ │
│  │  最近操作:                                                   │ │
│  │  14:35 write → RedemptionService.java (新增积分抵扣方法)     │ │
│  │  14:33 read  → RedemptionController.java                     │ │
│  │  14:32 bash  → git diff --stat                               │ │
│  │  14:30 read  → impl-plan-42.md                               │ │
│  └──────────────────────────────────────────────────────────────┘ │
│                                                                  │
│  ┌─Self-Fix循环状态────────────────────────────────────────────┐ │
│  │  循环次数: 0/3                                               │ │
│  │  状态: 首次编码中（尚未进入测试）                              │ │
│  └──────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
```

#### 页面三：审批中心

```
┌──────────────────────────────────────────────────────────────────┐
│  审批中心                                          [全部] [待审批]│
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌─待审批项────────────────────────────────────────────────────┐ │
│  │                                                              │ │
│  │  ┌───────────────────────────────────────────────────────┐  │ │
│  │  │ ⚠ Spec方案审批  ISSUE-42                              │  │ │
│  │  │                                                       │  │ │
│  │  │ 方案摘要:                                             │  │ │
│  │  │ · 新增 RedemptionPointDeductService 积分抵扣服务      │  │ │
│  │  │ · 修改 RedemptionController 新增 /deduct 接口         │  │ │
│  │  │ · 新增 point_deduction 数据库表                       │  │ │
│  │  │ · 影响文件: 5个                                       │  │ │
│  │  │                                                       │  │ │
│  │  │ Contract对齐: ✓ 与OpenAPI定义一致                     │  │ │
│  │  │ 风险评估: 中（涉及金额计算）                           │  │ │
│  │  │                                                       │  │ │
│  │  │ [查看完整方案]  [通过 ✓]  [打回 ✗]  [备注...]         │  │ │
│  │  └───────────────────────────────────────────────────────┘  │ │
│  │                                                              │ │
│  │  ┌───────────────────────────────────────────────────────┐  │ │
│  │  │ ⚠ Review确认  PR-108                                  │  │ │
│  │  │                                                       │  │ │
│  │  │ 审查结果:                                             │  │ │
│  │  │ · 安全: ✓ 通过（无OWASP风险）                         │  │ │
│  │  │ · 性能: ⚠ 建议（N+1查询风险，第42行）                 │  │ │
│  │  │ · 规范: ✓ 通过（命名/复杂度/注释均达标）              │  │ │
│  │  │                                                       │  │ │
│  │  │ [查看PR详情]  [确认合并 ✓]  [要求修改 ✗]              │  │ │
│  │  └───────────────────────────────────────────────────────┘  │ │
│  └──────────────────────────────────────────────────────────────┘ │
│                                                                  │
│  ┌─审批历史（最近7天）─────────────────────────────────────────┐ │
│  │ 日期       类型       Issue     结果   审批人   耗时        │ │
│  │ 02-21     Spec审批   #42      待审批  --      12min       │ │
│  │ 02-21     Review     #108     待确认  --      5min        │ │
│  │ 02-20     Spec审批   #41      ✓通过   张三    8min        │ │
│  │ 02-20     最终签收   #40      ✓签收   李四    3min        │ │
│  │ 02-19     Review     #107     ✗打回   张三    15min       │ │
│  └──────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
```

#### 页面四：Skills 管理

```
┌──────────────────────────────────────────────────────────────────┐
│  Skills管理                           [新建Skill] [导入] [导出]  │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌─筛选───────────────────────────────────────────────────────┐ │
│  │ 层级: [全部▾]  标签: [全部▾]  角色: [全部▾]  状态: [全部▾] │ │
│  └──────────────────────────────────────────────────────────────┘ │
│                                                                  │
│  ┌─Skills列表──────────────────────────────────────────────────┐ │
│  │                                                              │ │
│  │  名称              层级  版本   使用次数  上次使用   状态    │ │
│  │  ──────────────────────────────────────────────────────────  │ │
│  │  coding-standards   L1   v2.1   342次    今天      ● 启用   │ │
│  │  security-audit     L1   v1.5   128次    今天      ● 启用   │ │
│  │  scope-guard        L1   v1.0   215次    今天      ● 启用   │ │
│  │  redemption-rules   L2   v3.2   89次     今天      ● 启用   │ │
│  │  benefits-logic     L2   v2.0   67次     昨天      ● 启用   │ │
│  │  jd-adapter         L2   v1.3   45次     2天前     ● 启用   │ │
│  │  sprint-42-context  L3   v1     12次     今天      ● 启用   │ │
│  │  sprint-41-context  L3   v3     0次      5天前     ○ 归档   │ │
│  │                                                              │ │
│  │  共 32 个Skills  本月新增: 6  月度目标: ≥5 ✓                 │ │
│  └──────────────────────────────────────────────────────────────┘ │
│                                                                  │
│  ┌─Skill详情: redemption-rules────────────────────────────────┐ │
│  │                                                              │ │
│  │  描述: 兑换模块业务规则与边界条件                             │ │
│  │  标签: domain, redemption, L2                                │ │
│  │  适用角色: Coding / Test / Review                            │ │
│  │  模型覆盖: 无（使用角色默认模型）                             │ │
│  │  文件大小: 2.4KB                                             │ │
│  │                                                              │ │
│  │  版本历史:                                                   │ │
│  │  v3.2 (02-18) 新增积分抵扣边界条件  [查看] [回滚]           │ │
│  │  v3.1 (02-10) 修复满减叠加规则      [查看]                  │ │
│  │  v3.0 (01-28) 重构兑换规则结构      [查看]                  │ │
│  │                                                              │ │
│  │  [编辑] [禁用] [查看源文件] [使用统计]                       │ │
│  └──────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
```

#### 页面五：KPI Dashboard

```
┌──────────────────────────────────────────────────────────────────┐
│  KPI Dashboard                    时间范围: [本周▾]  [导出报告]  │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌─核心指标卡片──────────────────────────────────────────────┐  │
│  │ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐       │  │
│  │ │ FPR      │ │Self-Fix  │ │Coverage  │ │Token/PSP │       │  │
│  │ │ 78%      │ │ 87%      │ │ 82%      │ │ 12%      │       │  │
│  │ │ 目标:80% │ │ 目标:85% │ │ 目标:80% │ │ 目标:<15%│       │  │
│  │ │ ⚠ 接近   │ │ ✓ 达标   │ │ ✓ 达标   │ │ ✓ 达标   │       │  │
│  │ └──────────┘ └──────────┘ └──────────┘ └──────────┘       │  │
│  │ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐       │  │
│  │ │H-Touch   │ │Smoke     │ │Plan Appr.│ │Review    │       │  │
│  │ │ 65min    │ │ 92%      │ │ 80%      │ │ Acc 93%  │       │  │
│  │ │ 目标:<90 │ │ 目标:90% │ │ 目标:75% │ │ FP<10%   │       │  │
│  │ │ ✓ 达标   │ │ ✓ 达标   │ │ ✓ 达标   │ │ ✓ 达标   │       │  │
│  │ └──────────┘ └──────────┘ └──────────┘ └──────────┘       │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                  │
│  ┌─趋势图（FPR + Coverage 7日趋势）───────────────────────────┐ │
│  │  100%│                                                      │ │
│  │   90%│          ·····  ·····                                 │ │
│  │   80%│    ·····              ·····  ←── Coverage 82%        │ │
│  │   70%│····                         ····  ←── FPR 78%       │ │
│  │   60%│─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ← 警戒线          │ │
│  │   50%├────┬────┬────┬────┬────┬────┬────                   │ │
│  │      Mon  Tue  Wed  Thu  Fri  Sat  Sun                     │ │
│  └──────────────────────────────────────────────────────────────┘ │
│                                                                  │
│  ┌─Token成本分析───────────────┐ ┌─Agent工作量分布────────────┐ │
│  │ 本周总Token: 2,450,000      │ │ Coding   ████████ 45%     │ │
│  │ 总成本: ¥896               │ │ Test     █████  28%       │ │
│  │ 等价PSP: ¥7,500            │ │ Review   ██  12%          │ │
│  │ 节省率: 88%                │ │ Spec     █  8%            │ │
│  │                             │ │ Doc      █  5%            │ │
│  │ 按角色:                     │ │ Orch     ▏ 1%            │ │
│  │ Coding  ¥420 (47%)         │ │ Smoke    ▏ 1%            │ │
│  │ Review  ¥185 (21%)         │ │                            │ │
│  │ Spec    ¥152 (17%)         │ │                            │ │
│  │ 其他    ¥139 (15%)         │ │                            │ │
│  └─────────────────────────────┘ └────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
```

### 6.5 数据模型

#### 核心表设计

```sql
-- Agent实例表
CREATE TABLE agents (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    role            VARCHAR(20) NOT NULL,      -- orchestrator/spec/coding/test/review/smoke/doc
    status          VARCHAR(20) NOT NULL,      -- running/idle/waiting/error/stopped
    model           VARCHAR(50) NOT NULL,      -- claude-opus-4/claude-sonnet-4
    config          JSONB NOT NULL,            -- AgentConfig序列化
    skills_loaded   TEXT[],                    -- 当前加载的Skills名称列表
    current_task_id UUID REFERENCES tasks(id),
    started_at      TIMESTAMP WITH TIME ZONE,
    last_active_at  TIMESTAMP WITH TIME ZONE,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- 任务表
CREATE TABLE tasks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    jira_id         VARCHAR(20) NOT NULL,      -- ISSUE-42
    title           VARCHAR(500) NOT NULL,
    description     TEXT,
    status          VARCHAR(20) NOT NULL,      -- pending/parsing/in_progress/completed/failed
    dag             JSONB,                     -- TaskDAG序列化
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    completed_at    TIMESTAMP WITH TIME ZONE,
    total_duration  INTERVAL,
    total_tokens    BIGINT DEFAULT 0,
    total_cost      DECIMAL(10,4) DEFAULT 0
);

-- 任务阶段表（Pipeline每个节点）
CREATE TABLE task_stages (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id         UUID NOT NULL REFERENCES tasks(id),
    stage_name      VARCHAR(20) NOT NULL,      -- parse/spec/approve/code/test/review/smoke/doc/signoff
    agent_role      VARCHAR(20) NOT NULL,
    status          VARCHAR(20) NOT NULL,      -- pending/running/completed/failed/blocked
    input_payload   JSONB,                     -- 上游Agent的输出
    output_payload  JSONB,                     -- 本阶段Agent的输出
    started_at      TIMESTAMP WITH TIME ZONE,
    completed_at    TIMESTAMP WITH TIME ZONE,
    duration        INTERVAL,
    tokens_used     BIGINT DEFAULT 0,
    turns_used      INT DEFAULT 0,
    self_fix_count  INT DEFAULT 0              -- Self-Fix循环次数
);

-- Skills表
CREATE TABLE skills (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(100) NOT NULL UNIQUE,
    layer           VARCHAR(5) NOT NULL,       -- L1/L2/L3
    description     TEXT,
    tags            TEXT[],
    applicable_roles TEXT[],                   -- 适用的Agent角色
    model_override  VARCHAR(50),               -- Per-skill模型覆盖
    content_hash    VARCHAR(64),               -- 内容哈希（变更检测）
    usage_count     INT DEFAULT 0,
    last_used_at    TIMESTAMP WITH TIME ZONE,
    status          VARCHAR(20) DEFAULT 'active', -- active/archived/deprecated
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Skill版本表
CREATE TABLE skill_versions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    skill_id        UUID NOT NULL REFERENCES skills(id),
    version         VARCHAR(20) NOT NULL,
    content         TEXT NOT NULL,             -- Skill完整内容（YAML+Markdown）
    change_summary  TEXT,
    author          VARCHAR(100),
    git_commit_sha  VARCHAR(40),
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- KPI指标表
CREATE TABLE kpi_metrics (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    metric_name     VARCHAR(50) NOT NULL,      -- fpr/self_fix_rate/coverage/...
    agent_role      VARCHAR(20),               -- 关联的Agent角色
    task_id         UUID REFERENCES tasks(id),
    value           DECIMAL(10,4) NOT NULL,
    unit            VARCHAR(20),               -- percent/minutes/count/currency
    recorded_at     TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
CREATE INDEX idx_kpi_metrics_name_time ON kpi_metrics(metric_name, recorded_at);
CREATE INDEX idx_kpi_metrics_role ON kpi_metrics(agent_role, recorded_at);

-- 审计日志表
CREATE TABLE audit_logs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_role      VARCHAR(20) NOT NULL,
    action_type     VARCHAR(50) NOT NULL,      -- tool_call/skill_invoke/model_switch/...
    action_detail   JSONB NOT NULL,            -- 操作详情
    task_id         UUID REFERENCES tasks(id),
    session_id      VARCHAR(100),              -- SkillKit SessionManager的session_id
    risk_level      VARCHAR(10),               -- low/medium/high/critical
    timestamp       TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
CREATE INDEX idx_audit_logs_time ON audit_logs(timestamp);
CREATE INDEX idx_audit_logs_risk ON audit_logs(risk_level, timestamp);

-- 人工审批门控表
CREATE TABLE human_gates (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    gate_type       VARCHAR(30) NOT NULL,      -- spec_approval/review_confirm/final_signoff
    task_id         UUID NOT NULL REFERENCES tasks(id),
    stage_id        UUID NOT NULL REFERENCES task_stages(id),
    content         JSONB NOT NULL,            -- 待审批内容
    status          VARCHAR(20) DEFAULT 'pending', -- pending/approved/rejected/timeout
    reviewer        VARCHAR(100),
    review_comment  TEXT,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    reviewed_at     TIMESTAMP WITH TIME ZONE,
    timeout_at      TIMESTAMP WITH TIME ZONE   -- 审批超时时间
);
```

### 6.6 API 设计

#### RESTful API 规范

**Base URL**: `/api/v1`

**认证**: JWT Token（内部SSO集成）

#### Agent 管理

```
GET    /agents                     # 获取所有Agent状态
GET    /agents/{role}              # 获取指定角色Agent详情
PUT    /agents/{role}/config       # 更新Agent配置
POST   /agents/{role}/start       # 启动Agent
POST   /agents/{role}/stop        # 停止Agent
POST   /agents/{role}/restart     # 重启Agent
GET    /agents/{role}/session     # 获取Agent当前会话
```

#### 任务生命周期

```
GET    /tasks                      # 任务列表（分页/筛选）
POST   /tasks                      # 创建任务（手动触发）
GET    /tasks/{id}                 # 任务详情
GET    /tasks/{id}/stages          # 任务Pipeline各阶段
GET    /tasks/{id}/stages/{stage}  # 阶段详情
GET    /tasks/{id}/logs            # 任务执行日志
POST   /tasks/{id}/cancel         # 取消任务
```

#### Skills 管理

```
GET    /skills                     # Skills列表（按层级/标签/角色筛选）
POST   /skills                     # 创建新Skill
GET    /skills/{name}              # Skill详情
PUT    /skills/{name}              # 更新Skill内容
DELETE /skills/{name}              # 归档Skill
GET    /skills/{name}/versions     # Skill版本历史
POST   /skills/{name}/rollback    # 回滚到指定版本
GET    /skills/stats               # Skills使用统计
```

#### 审批管理

```
GET    /gates                      # 待审批列表
GET    /gates/{id}                 # 审批项详情
POST   /gates/{id}/approve        # 通过审批
POST   /gates/{id}/reject         # 拒绝审批（附带原因）
GET    /gates/history              # 审批历史
```

#### KPI 查询

```
GET    /kpi/summary                # KPI概览（当前值 + 趋势）
GET    /kpi/metrics/{name}         # 特定指标时序数据
GET    /kpi/report                 # 生成周/月报告
GET    /kpi/compare                # PSP对比分析
```

#### 审计查询

```
GET    /audit/logs                 # 审计日志查询（时间/角色/风险级别）
GET    /audit/security             # 安全事件列表
GET    /audit/export               # 导出审计报告
```

#### 止损控制

```
GET    /circuit-breaker/status     # 当前止损状态
POST   /circuit-breaker/trigger    # 手动触发止损（Level 1/2/3）
POST   /circuit-breaker/release   # 解除止损
GET    /circuit-breaker/history    # 止损历史记录
```

### 6.7 平台与 SkillKit 的集成方式

管理平台不修改 SkillKit 核心代码，而是通过以下方式集成：

```
┌─────────────────────────────────────┐
│      Agent Management Platform       │
│             (FastAPI)                │
├─────────────────────────────────────┤
│                                     │
│  1. Python Import 直接调用          │
│     from skillkit import AgentRunner │
│     from skillkit import SkillsEngine│
│     from skillkit import EventBus    │
│                                     │
│  2. EventBus 事件订阅               │
│     runner.events.on("agent_end",   │
│         platform_handler)           │
│                                     │
│  3. Session 数据读取                │
│     SessionManager.load_existing()  │
│                                     │
│  4. Extension 系统扩展              │
│     KPICollectorExtension           │
│     AuditLogExtension               │
│                                     │
└─────────────────────────────────────┘
```

**集成原则**：
- **Import不Fork**：平台直接 `import skillkit`，不fork SkillKit代码
- **Event不Polling**：通过EventBus订阅事件，不轮询Agent状态
- **Extension不Hack**：新功能通过Extension系统注入，不修改Agent核心循环
- **Session不重建**：直接使用SessionManager读取Agent执行记录，不另建日志系统

---

## 七、实施优先级与路线图

### Phase 1：核心Agent运行时（第1-4周）

**目标**：完成多Agent编排层 + 2个Agent试运行

**交付物**：

| 周次 | 任务 | 产出 |
|------|------|------|
| W1 | WorkforceOrchestrator骨架 | `workforce/orchestrator.py`、`workforce/message_bus.py` |
| W1 | HumanGate机制 | `workforce/human_gate.py` |
| W2 | Review Agent Skills + 配置 | `skills/L1/security-audit.md`、`skills/L1/perf-audit.md`、`skills/L1/standards-audit.md` |
| W2 | Test Agent Skills + 配置 | `skills/L2/unit-test-runner.md`、`skills/L2/contract-test.md` |
| W3 | KPI采集Extension | `extensions/kpi_collector.py` |
| W3 | 基础审计日志Extension | `extensions/audit_logger.py` |
| W4 | Review + Test Agent联调 | 对接GitLab CI/CD，处理真实PR |

**里程碑判定**：
- Review Agent覆盖100% PR，误报率 < 20%
- Test Agent在至少1个核心模块达到 ≥ 75% 覆盖率
- KPI数据开始采集（4周基线数据）

### Phase 2：全Agent闭环（第5-8周）

**目标**：7个Agent完整上线 + GitLab/Jira集成 + 人工门控

**交付物**：

| 周次 | 任务 | 产出 |
|------|------|------|
| W5 | Orchestrator Agent Skills | `skills/L2/task-parser.md`、`skills/L2/task-router.md` |
| W5 | Spec Agent Skills | `skills/L2/impl-plan-generator.md`、`skills/L2/contract-validator.md` |
| W6 | Coding Agent Skills | `skills/L2/code-generator.md`、`skills/L1/scope-guard.md` + L2领域Skills |
| W6 | GitLab/Jira集成Skills | `skills/L1/gitlab-mr.md`、`skills/L1/jira-sync.md` |
| W7 | Smoke Agent + DockerBashRuntime | `runtime/docker.py` + `skills/L2/e2e-validator.md` |
| W7 | Doc Agent Skills | `skills/L2/changelog-generator.md`、`skills/L3/skill-extractor.md` |
| W8 | 全链路联调 | Jira Issue → 7 Agent链 → MR合并 → Jira关闭 |

**里程碑判定**：
- 首个完整任务：Issue → Agent链 → 代码合并，Human-Touch < 90min
- L2领域Skills库建立（兑换/权益/组套）
- Contract-First工作流在 ≥ 1个模块运行
- PSP对比数据显示明确优势

### Phase 3：管理平台（第9-12周）

**目标**：运营Dashboard全功能上线

**交付物**：

| 周次 | 任务 | 产出 |
|------|------|------|
| W9 | 平台后端骨架 | FastAPI项目、数据模型、Agent/Task API |
| W9 | 前端骨架 | React + Ant Design Pro项目、路由、布局 |
| W10 | 集群总览 + 任务Pipeline | 前后端实现 + WebSocket实时推送 |
| W10 | 审批中心 | 与HumanGate集成、Slack通知 |
| W11 | Skills管理 | CRUD + 版本历史 + Git集成 |
| W11 | KPI Dashboard | Prometheus集成 + Grafana嵌入 + 趋势图 |
| W12 | 审计日志 + 止损控制台 | 日志查询 + 三级止损操作 |

**里程碑判定**：
- 平台覆盖所有8个功能模块
- 架构师/TL能通过Dashboard完成日常管控操作
- KPI数据可视化，周报/月报自动生成
- 三级止损机制可在平台上操作

### 路线图总览

```
Week:  1    2    3    4    5    6    7    8    9   10   11   12
       ├────────────────┤ ├────────────────┤ ├────────────────┤
Phase: │  Phase 1       │ │  Phase 2       │ │  Phase 3       │
       │  核心运行时     │ │  全Agent闭环   │ │  管理平台       │
       │                │ │                │ │                │
       │ WorkforceOrch  │ │ Orch+Spec+Code │ │ 后端+前端       │
       │ Review+Test    │ │ Smoke+Doc      │ │ Dashboard       │
       │ KPI Extension  │ │ GitLab/Jira    │ │ Skills管理      │
       │ 基线数据采集    │ │ Docker沙箱     │ │ KPI可视化       │
       │                │ │ 全链路联调     │ │ 审计+止损       │
       ├────────────────┤ ├────────────────┤ ├────────────────┤
       M1 Go/No-Go ──────→ M2 Go/No-Go ──────→ M3 Go/No-Go
```

### Go/No-Go 决策门

| 里程碑 | 时间 | 通过标准 | 未通过处置 |
|--------|------|---------|-----------|
| M1 | 第4周 | Review误报<20%；Test覆盖≥75%；KPI数据连续4周 | 延长Shadow期，优化Skills |
| M2 | 第8周 | 端到端完成；FPR≥70%；PSP对比有明确优势 | 缩小范围，聚焦1个模块 |
| M3 | 第12周 | 7 Agent全部上线；核心Smoke≥90%；Token-vs-PSP<20% | 暂停PSP退出，保持Agent运行 |

---

## 八、结论与建议

### 8.1 核心结论

1. **SkillKit是正确的技术选型**。其"Skill First"理念与SITC数字员工的"知识驱动"哲学高度吻合——Agent的能力不是靠硬编码，而是靠Skills注入。这使得Agent能力可以持续进化，不需要修改代码。

2. **最大投入在编排层，而非Agent本身**。单个Agent的能力SkillKit已基本满足，真正的工作量在于让7个Agent协同工作——任务DAG、消息传递、人工门控、KPI采集。

3. **管理平台是运营基础设施**。没有Dashboard，架构师就无法有效管控Agent集群。平台不是锦上添花，而是"Human for Decision"原则的技术保障。

### 8.2 关键建议

| # | 建议 | 理由 |
|---|------|------|
| 1 | **Phase 1 先上Review + Test Agent** | 最低风险入口——在现有CI/CD流程末端增加自动审查，不影响现有研发流程 |
| 2 | **Skills先做L1通用层** | L1标准（编码规范/安全审计/Git工作流）适用于所有角色，投入产出比最高 |
| 3 | **编排层从进程内开始** | Phase 1用 `asyncio.Queue` 实现MessageBus，Phase 2再考虑Redis分布式——避免过早引入基础设施复杂度 |
| 4 | **KPI从第一天开始采集** | 即使Phase 1只有2个Agent，也要开始采集基线数据——M1的Go/No-Go决策依赖连续4周数据 |
| 5 | **Docker沙箱可以延后** | Phase 1/2的Review和Test Agent可以在受控主机环境运行，Docker沙箱主要服务Smoke Agent（Phase 2后期） |
| 6 | **管理平台MVP足够** | Phase 3的平台不追求美观，追求功能完整——能看到Agent状态、能审批、能看KPI即可 |

### 8.3 风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| SkillKit升级导致API Breaking Change | 编排层/Extension需要适配 | 固定SkillKit版本；通过Extension系统解耦；避免monkey-patch |
| Agent产出质量不达标（FPR < 60%） | 无法替代PSP | Phase 1充分验证；L2领域Skills持续优化；保留PSP "保险阀" |
| 人工审批成为瓶颈 | Human-Touch时间超标 | 审批超时自动通知；移动端审批支持；逐步放宽低风险任务的审批要求 |
| Token成本超预期 | Token-vs-PSP比率超标 | Per-skill模型切换（日常用Sonnet，复杂用Opus）；上下文压缩策略优化 |
| 跨Agent编排层复杂度 | 开发周期延长 | 从最小可用编排开始（线性Pipeline → DAG → 并行）；渐进式增加复杂度 |

---

> **下一步行动**：
> 1. 团队Review本文档，对齐技术方向
> 2. 建立 `workforce/` 模块目录，开始Gap 1（WorkforceOrchestrator）开发
> 3. 编写Phase 1的Review Agent和Test Agent的L1 Skills
> 4. 部署KPI采集Extension，开始基线数据采集
