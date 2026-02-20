# 硅基Agent数字码农集群 · 技术落地方案 v1.0

> **SITC Trading Team · Engineering Implementation Spec**
> Starbucks China Innovation Technology Center · 2025
> **密级：内部技术文档 · 面向架构师/TL/DevOps**

---

## 文档定位

本文档是《硅基Agent战略演进白皮书》和《数字员工集群运作全书（Operations Manual）》的**工程落地配套**。白皮书回答"为什么做"和"做什么"，本文档回答**"怎么做"和"用什么做"**。

所有配置、模板、Pipeline定义均基于MVP实验（营销配置接口，1 Commander + 3 Agents, 48h, 100% Coverage）的验证结论迭代而来。

**关键约束对齐（来自Operations Manual最新KPI）**：

| 指标 | 设计方案v1.0目标 | Operations Manual目标（更激进） | 本方案对齐 |
|------|---------------|---------------------------|---------|
| Token-vs-PSP | < 15% | **< 10%** | < 10% |
| Human-Touch Time | < 90 min/task | **< 15 min/task** | < 15 min（标准任务） |
| Agent-FPR | ≥ 80% | ≥ 80%（< 60%触发优化） | ≥ 80% |
| Self-Fix Rate | ≥ 85% | ≥ 85%（< 80%检查Context） | ≥ 85% |
| Contract Deviation | 0% | **0%（Any deviation = Immediate Stop）** | 0% |

---

## 目录

1. [技术架构总览](#一技术架构总览)
2. [基础设施层：Docker Sandbox & 工具链](#二基础设施层docker-sandbox--工具链)
3. [Skills知识体系：工程化实现](#三skills知识体系工程化实现)
4. [Agent Prompt工程：7角色System Prompt模板](#四agent-prompt工程7角色system-prompt模板)
5. [GitLab CI/CD Pipeline：Agent流水线集成](#五gitlab-cicd-pipelineagent流水线集成)
6. [Jira → Agent → GitLab 自动化链路](#六jira--agent--gitlab-自动化链路)
7. [KPI数据采集与Dashboard实现](#七kpi数据采集与dashboard实现)
8. [Phase 1 执行手册：Review Agent首发](#八phase-1-执行手册review-agent首发)
9. [MVP复盘与Phase 2关键调整](#九mvp复盘与phase-2关键调整)
10. [附录：配置文件与模板全集](#十附录配置文件与模板全集)

---

## 一、技术架构总览

### 1.1 系统架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                        Human Commander Layer                     │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌───────────────┐   │
│  │   Jira   │  │  GitLab  │  │  Slack   │  │  Dashboard    │   │
│  │  Issue   │  │  MR/PR   │  │  Alert   │  │  (Grafana)    │   │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └───────┬───────┘   │
│       │              │              │                │           │
├───────┼──────────────┼──────────────┼────────────────┼───────────┤
│       ▼              ▼              ▼                ▼           │
│                  Orchestration Layer                              │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │              Orchestrator Agent（编排官）                   │    │
│  │  ┌─────────┐ ┌──────────┐ ┌───────────┐ ┌──────────┐  │    │
│  │  │ Issue   │ │ DAG      │ │ Agent     │ │ Progress │  │    │
│  │  │ Parser  │ │ Builder  │ │ Dispatcher│ │ Monitor  │  │    │
│  │  └─────────┘ └──────────┘ └───────────┘ └──────────┘  │    │
│  └─────────────────────────────────────────────────────────┘    │
│                                                                  │
├──────────────────────────────────────────────────────────────────┤
│                     Agent Execution Layer                         │
│                                                                  │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐           │
│  │  Spec   │  │ Coding  │  │  Test   │  │ Review  │           │
│  │  Agent  │  │ Agent   │  │  Agent  │  │  Agent  │           │
│  │         │  │  ×2-4   │  │         │  │         │           │
│  └────┬────┘  └────┬────┘  └────┬────┘  └────┬────┘           │
│       │            │            │             │                 │
│  ┌────┴────┐  ┌────┴────┐  ┌───┴─────┐  ┌───┴──────┐         │
│  │  Smoke  │  │   Doc   │  │ Sandbox │  │ SonarQube│         │
│  │  Agent  │  │  Agent  │  │ Runtime │  │ Scanner  │         │
│  └─────────┘  └─────────┘  └─────────┘  └──────────┘         │
│                                                                  │
├──────────────────────────────────────────────────────────────────┤
│                     Infrastructure Layer                          │
│  ┌──────────┐  ┌───────────┐  ┌──────────┐  ┌──────────────┐  │
│  │  Docker  │  │  Skills   │  │ Contract │  │  Metrics     │  │
│  │ Sandbox  │  │   Repo    │  │   Repo   │  │  Collector   │  │
│  │ (隔离)   │  │ (Git管理) │  │ (OpenAPI)│  │ (Prometheus) │  │
│  └──────────┘  └───────────┘  └──────────┘  └──────────────┘  │
└──────────────────────────────────────────────────────────────────┘
```

### 1.2 技术选型决策

| 组件 | 选型 | 理由 |
|------|------|------|
| Agent Runtime | Claude Code CLI (claude) | 原生支持 execute/read/write/execute_script，Skill First 哲学对齐 |
| Agent IDE/Sandbox | Docker + JetBrains Reno Remote | Corporate_Std_v4.2 镜像，与人类开发环境对等 |
| 代码托管 | GitLab (现有) | 直接复用现有 CI/CD 基础设施 |
| 任务管理 | Jira (现有) | Webhook 触发 Agent 链路 |
| 契约管理 | OpenAPI 3.0 YAML (Git版本控制) | Contract-First 基座 |
| Skills存储 | Git Repo (Markdown文件) | 版本化、可审计、可回滚 |
| KPI采集 | Prometheus + Custom Exporter | 实时采集 Agent 运行数据 |
| Dashboard | Grafana | 可视化 KPI 矩阵 |
| 告警 | Slack Webhook | 实时推送异常事件 |
| 安全扫描 | Gitleaks + SonarQube | PII检测 + 代码质量门控 |

### 1.3 Agent Runtime选型详解：为什么是 Claude Code

```
Skill First 哲学映射：
━━━━━━━━━━━━━━━━━━━

汇报核心理念                          Claude Code 原生能力
─────────────                        ─────────────────
"Skills are new software"     →      Markdown Skills 注入 System Prompt
"CLIs are new API"            →      execute, read, write, execute_script
"Agents are new OS"           →      Agent 编排模式，支持 subagent 调度

关键优势：
1. Skills 不是函数调用，而是注入 System Prompt 的"软件说明书"
   → Claude Code 原生支持 .claude/skills/ 目录自动加载
2. 支持 Docker sandbox 隔离执行
3. 支持 MCP (Model Context Protocol) 扩展工具链
4. 支持 Team 模式（多 Agent 协同）
```

---

## 二、基础设施层：Docker Sandbox & 工具链

### 2.1 Agent Sandbox Docker镜像规格

```dockerfile
# Dockerfile.agent-sandbox
# 基于公司标准镜像 Corporate_Std_v4.2

FROM corporate-registry.starbucks.com/base/java-dev:v4.2

LABEL maintainer="SITC Trading Team"
LABEL description="Silicon Agent Sandbox - Isolated Execution Environment"
LABEL version="1.0"

# ============================================
# 编译环境（与人类开发环境严格对等）
# ============================================
ENV JAVA_VERSION=17
ENV MAVEN_VERSION=3.9.6
ENV NODE_VERSION=18
ENV GRADLE_VERSION=8.5

# JDK（锁定版本）
RUN sdk install java 17.0.9-tem && sdk default java 17.0.9-tem

# Build工具
RUN sdk install maven ${MAVEN_VERSION} && \
    sdk install gradle ${GRADLE_VERSION}

# Node.js（前端/工具链）
RUN nvm install ${NODE_VERSION} && nvm alias default ${NODE_VERSION}

# ============================================
# 代码质量工具链
# ============================================

# Linter & Formatter（严格模式）
RUN npm install -g eslint@8 prettier@3 && \
    pip install pylint black flake8

# SonarQube Scanner
RUN curl -sL https://binaries.sonarsource.com/Distribution/sonar-scanner-cli/sonar-scanner-cli-5.0.tar.gz | tar xz -C /opt && \
    ln -s /opt/sonar-scanner-*/bin/sonar-scanner /usr/local/bin/sonar-scanner

# Gitleaks（敏感信息检测）
RUN curl -sSfL https://github.com/gitleaks/gitleaks/releases/download/v8.18.0/gitleaks_8.18.0_linux_x64.tar.gz | tar xz -C /usr/local/bin

# OpenAPI工具链
RUN npm install -g @redocly/cli@1.5 openapi-generator-cli@2.7

# ============================================
# 安全约束
# ============================================

# 创建受限用户（非root运行）
RUN useradd -m -s /bin/bash agent && \
    mkdir -p /workspace /skills /contracts && \
    chown -R agent:agent /workspace /skills /contracts

# 禁止网络访问（仅允许内部 GitLab 和 API）
# 通过 Docker network policy 实现，见 docker-compose.yml

USER agent
WORKDIR /workspace

# ============================================
# 依赖锁定（Frozen Dependencies）
# ============================================
COPY maven-settings.xml /home/agent/.m2/settings.xml
COPY npm-lock.json /workspace/
COPY requirements-lock.txt /workspace/

# 预安装锁定版本的依赖
RUN mvn dependency:go-offline -s /home/agent/.m2/settings.xml || true
```

### 2.2 Docker Compose编排

```yaml
# docker-compose.agent-cluster.yml

version: '3.9'

services:
  # =============================================
  # Agent Sandbox 模板（按需实例化）
  # =============================================
  agent-sandbox:
    build:
      context: .
      dockerfile: Dockerfile.agent-sandbox
    volumes:
      # 只读挂载：Skills、契约库
      - ./skills:/skills:ro
      - ./contracts:/contracts:ro
      # 读写挂载：工作区（每个Agent实例独立）
      - agent-workspace-${AGENT_ID}:/workspace
    networks:
      - agent-internal
    environment:
      - AGENT_ROLE=${AGENT_ROLE}
      - AGENT_ID=${AGENT_ID}
      - GITLAB_TOKEN=${GITLAB_AGENT_TOKEN}
      - GITLAB_URL=https://gitlab.internal.starbucks.com
      - SONARQUBE_URL=https://sonar.internal.starbucks.com
      - SONARQUBE_TOKEN=${SONARQUBE_TOKEN}
    deploy:
      resources:
        limits:
          cpus: '2'
          memory: 4G
        reservations:
          cpus: '1'
          memory: 2G
    security_opt:
      - no-new-privileges:true
    read_only: false
    tmpfs:
      - /tmp:size=1G

  # =============================================
  # KPI Metrics Collector
  # =============================================
  metrics-collector:
    image: prom/prometheus:latest
    volumes:
      - ./config/prometheus.yml:/etc/prometheus/prometheus.yml
      - prometheus-data:/prometheus
    ports:
      - "9090:9090"
    networks:
      - agent-internal

  # =============================================
  # Dashboard
  # =============================================
  grafana:
    image: grafana/grafana:latest
    volumes:
      - ./config/grafana/dashboards:/var/lib/grafana/dashboards
      - grafana-data:/var/lib/grafana
    ports:
      - "3000:3000"
    networks:
      - agent-internal
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=${GRAFANA_ADMIN_PASSWORD}

networks:
  agent-internal:
    driver: bridge
    internal: true  # 禁止外部网络访问
    ipam:
      config:
        - subnet: 172.28.0.0/16

  # 仅允许访问 GitLab 和 API 的出口网络
  agent-egress:
    driver: bridge

volumes:
  prometheus-data:
  grafana-data:
```

### 2.3 网络隔离策略

```
Agent Sandbox 网络策略（零信任）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

允许出站：
  ✅ gitlab.internal.starbucks.com:443   （代码推送/拉取）
  ✅ sonar.internal.starbucks.com:443    （质量扫描）
  ✅ api.anthropic.com:443               （Claude API）
  ✅ nexus.internal.starbucks.com:443    （Maven/npm制品库）

禁止出站：
  ✗ 任何公网地址
  ✗ 生产数据库
  ✗ 其他团队的 GitLab 仓库
  ✗ 任何未在白名单的内部服务

Agent间通信：
  ✅ 通过共享 GitLab Repo（MR/Issue）间接通信
  ✗ 禁止 Agent 间直接网络通信
```

---

## 三、Skills知识体系：工程化实现

### 3.1 Skills仓库目录结构

```
skills-repo/
├── README.md                          # Skills 治理规范
├── CHANGELOG.md                       # Skills 变更记录
│
├── L1-common/                         # Layer 1：公共规范（所有Agent共享）
│   ├── code-standards.md              # 代码规范 Skill
│   ├── security-coding.md             # 安全编码 Skill (OWASP Top 10)
│   ├── naming-conventions.md          # 命名规范 Skill
│   ├── git-workflow.md                # Git 工作流 Skill
│   ├── error-handling.md              # 异常处理规范 Skill
│   └── logging-standards.md           # 日志规范 Skill
│
├── L2-domain/                         # Layer 2：领域业务（按模块分区）
│   ├── redemption/                    # 核销模块
│   │   ├── redemption-rules.md        # 核销规则 Skill
│   │   ├── redemption-edge-cases.md   # 核销边界Case Skill
│   │   └── inventory-deduction.md     # 库存扣减逻辑 Skill
│   ├── benefits/                      # 权益金模块
│   │   ├── coupon-rules.md            # 优惠券规则 Skill
│   │   ├── gift-card-rules.md         # 礼品卡规则 Skill
│   │   └── stars-rewards.md           # 星星奖励规则 Skill
│   ├── platform-adapters/             # 平台适配模块
│   │   ├── jd-integration.md          # 京东集成 Skill
│   │   ├── douyin-integration.md      # 抖音集成 Skill
│   │   └── miniprogram-integration.md # 小程序集成 Skill
│   └── bundling/                      # 搭售模块
│       └── bundling-rules.md          # 搭售规则 Skill
│
├── L3-project/                        # Layer 3：项目迭代（按Sprint更新）
│   ├── current-sprint/
│   │   ├── sprint-dod.md              # 当前Sprint完成标准
│   │   ├── api-changes.md             # 本迭代接口变更
│   │   └── tech-decisions.md          # 本迭代技术决策记录
│   └── archive/                       # 历史Sprint归档
│       ├── sprint-2025-01/
│       └── sprint-2025-02/
│
├── agent-specific/                    # Agent角色专属Skills
│   ├── orchestrator/
│   │   ├── task-decomposition.md      # 任务分解方法论
│   │   └── dependency-analysis.md     # 依赖关系分析
│   ├── spec-agent/
│   │   ├── contract-first.md          # Contract-First设计方法
│   │   ├── impact-analysis.md         # 影响范围分析
│   │   └── plan-template.md           # Implementation Plan模板
│   ├── coding-agent/
│   │   ├── self-fix-patterns.md       # 自修复模式库
│   │   └── code-patterns.md           # 代码模式库
│   ├── test-agent/
│   │   ├── test-strategy.md           # 测试策略
│   │   ├── contract-test.md           # 契约测试方法
│   │   └── scenario-library.md        # 业务场景库
│   ├── review-agent/
│   │   ├── review-checklist.md        # 审计检查清单
│   │   ├── bug-pattern-library.md     # 历史Bug模式库
│   │   └── sonarqube-rules.md         # SonarQube规则映射
│   ├── smoke-agent/
│   │   ├── smoke-scenarios.md         # 集成场景库
│   │   └── mock-server-config.md      # Mock平台配置
│   └── doc-agent/
│       ├── doc-templates.md           # 文档模板
│       └── skill-extraction.md        # Skills提炼方法论
│
└── meta/                              # Skills治理元数据
    ├── governance-rules.md            # 纳入/淘汰标准
    ├── review-checklist.md            # Skills审批检查清单
    └── usage-tracking.md              # Skills使用频率追踪
```

### 3.2 Skill文件标准格式

每个Skill文件遵循统一的Markdown格式，确保Agent可精确解析：

```markdown
---
skill_id: L2-REDEMPTION-001
skill_name: 核销规则
version: 1.3
last_updated: 2025-02-15
owner: zhang.architect
applicable_agents: [coding-agent, test-agent, spec-agent]
domain: redemption
priority: critical
usage_count: 47
---

# 核销规则 Skill

## 核心规则

### 规则1：核销幂等性
同一笔订单的核销操作必须是幂等的。重复核销请求应返回上一次核销结果，
不得重复扣减库存。

实现要点：
- 使用 `order_id` + `redemption_type` 作为幂等键
- 在 Redis 中设置 24h TTL 的幂等锁
- 锁冲突时返回 HTTP 409 Conflict 和上次核销凭证

### 规则2：并发扣减
库存扣减必须使用乐观锁（版本号机制）或分布式锁。

实现要点：
- 优先使用 `UPDATE ... SET stock = stock - 1 WHERE stock > 0` 原子操作
- 并发量 > 100 QPS 的场景使用 Redis 预扣减 + DB 异步落库
- 禁止先 SELECT 再 UPDATE 的非原子操作

## 边界Case

| Case | 输入 | 期望行为 | 注意事项 |
|------|------|---------|---------|
| 库存为0 | 核销请求 | 返回 HTTP 422, error_code: STOCK_EMPTY | 不得返回500 |
| 订单已取消 | 核销请求 | 返回 HTTP 422, error_code: ORDER_CANCELLED | 需先校验订单状态 |
| 跨天订单 | 23:59下单, 00:01核销 | 按下单日期计算 | T+1日期边界处理 |

## 代码示例（参考模式）

```java
// 幂等核销方法签名
@Transactional
public RedemptionResult redeem(String orderId, RedemptionType type) {
    // 1. 幂等检查
    String idempotentKey = orderId + ":" + type.name();
    RedemptionResult cached = redisTemplate.opsForValue().get(idempotentKey);
    if (cached != null) return cached;

    // 2. 订单状态校验
    Order order = orderRepository.findByIdOrThrow(orderId);
    if (order.getStatus() == OrderStatus.CANCELLED) {
        throw new BusinessException(ErrorCode.ORDER_CANCELLED);
    }

    // 3. 原子扣减
    int affected = stockRepository.deductStock(order.getSkuId(), 1);
    if (affected == 0) {
        throw new BusinessException(ErrorCode.STOCK_EMPTY);
    }

    // 4. 记录核销凭证
    RedemptionResult result = createRedemptionRecord(order, type);

    // 5. 缓存幂等结果
    redisTemplate.opsForValue().set(idempotentKey, result, 24, TimeUnit.HOURS);

    return result;
}
```

## 关联契约

- OpenAPI: `/api/v1/redemptions` (POST)
- AsyncAPI: `order.redeemed` (Event)

## 变更历史

| 版本 | 日期 | 变更内容 | 变更人 |
|------|------|---------|--------|
| 1.3 | 2025-02-15 | 增加跨天订单边界Case | zhang.architect |
| 1.2 | 2025-01-20 | 增加Redis预扣减方案 | wang.moduleowner |
| 1.0 | 2024-12-01 | 初始版本 | zhang.architect |
```

### 3.3 Skills注入机制

```
Skills 注入流程（对应 Skill First 哲学）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

                ┌─────────────────────┐
                │  Orchestrator 解析   │
                │  Issue → 识别涉及    │
                │  的业务模块          │
                └──────────┬──────────┘
                           │
                           ▼
                ┌─────────────────────┐
                │  Skills Router      │
                │  根据模块 → 选择    │
                │  L1 + L2 + L3 Skills│
                └──────────┬──────────┘
                           │
              ┌────────────┼────────────┐
              ▼            ▼            ▼
     ┌──────────────┐ ┌─────────┐ ┌──────────┐
     │ L1 公共规范   │ │L2 领域  │ │L3 项目   │
     │ (必须注入)    │ │(按模块) │ │(当前Sprint)│
     └──────┬───────┘ └────┬────┘ └─────┬────┘
            │              │             │
            └──────────────┴─────────────┘
                           │
                           ▼
              ┌─────────────────────────┐
              │  Agent System Prompt    │
              │  = Base Prompt          │
              │  + L1 Skills (全量)     │
              │  + L2 Skills (按模块)   │
              │  + L3 Skills (当前Sprint)│
              │  + Role-Specific Skills │
              └─────────────────────────┘

注入方式：通过 Claude Code 的 --system-prompt 参数
或 .claude/settings.json 中的 skills 配置
```

---

## 四、Agent Prompt工程：7角色System Prompt模板

### 4.1 Prompt模板架构

每个Agent的System Prompt由四层组成：

```
┌─────────────────────────────────┐
│  Layer 4: Task Context          │ ← 每次任务动态注入
│  (Issue内容/Plan/代码diff)       │
├─────────────────────────────────┤
│  Layer 3: Role-Specific Prompt  │ ← 角色专属指令
│  (角色职责/输出格式/禁止行为)    │
├─────────────────────────────────┤
│  Layer 2: Domain Skills         │ ← 按模块动态选择
│  (L2领域Skills + L3项目Skills)  │
├─────────────────────────────────┤
│  Layer 1: Base Prompt           │ ← 所有Agent共享
│  (L1公共规范 + 安全约束 + 输出协议)│
└─────────────────────────────────┘
```

### 4.2 Base Prompt（所有Agent共享）

```markdown
# SITC Digital Employee Base Protocol

## Identity
You are a certified digital employee of SITC Trading Team (Starbucks China
Innovation Technology Center). You operate under the "Human for Decision,
Agent for Execution" principle.

## Core Constraints
1. **Contract-First**: Any interface implementation must align 100% with
   OpenAPI contract. ANY deviation = BLOCKER. No exceptions.
2. **Scope-Bound**: Only modify files explicitly listed in the
   Implementation Plan. Zero tolerance for out-of-scope changes.
3. **Permission Protocol**: Push to feature branch ONLY. No direct merge
   to main/release. No force push. No branch deletion.
4. **Dependency Frozen**: Only use dependencies listed in the approved
   dependency manifest. Any new dependency requires Human approval.
5. **PII Protection**: Never log, hardcode, or expose PII (personal
   identifiable information). Gitleaks scan must pass.

## Output Protocol
All structured outputs must follow JSON format with the following envelope:
{
  "agent_role": "<your-role>",
  "agent_id": "<your-instance-id>",
  "task_id": "<jira-issue-key>",
  "timestamp": "<ISO-8601>",
  "output_type": "<plan|code|test|review|smoke|doc>",
  "status": "<success|failure|needs_human>",
  "payload": { ... }
}

## Escalation Protocol
When you encounter situations beyond your capability:
- Set status to "needs_human"
- Provide clear description of the blocker
- Suggest potential resolution paths
- Do NOT attempt to work around the issue autonomously
```

### 4.3 Review Agent Prompt（Phase 1 首发角色）

```markdown
# Review Agent — 审计官 System Prompt

## Role Definition
You are the Review Agent (审计官) of the SITC digital employee cluster.
Your role is to perform automated 3-dimensional code audits on Pull Requests,
producing structured review reports for human architects to sign off.

## The 3D Audit Framework

### Dimension 1: Security Audit (安全风险)
Scan for:
- SQL Injection: String concatenation in SQL queries
- XSS/CSRF: Unescaped user input in responses
- Authorization bypass: Missing permission checks
- Hardcoded secrets: Passwords, API keys, PII in code
- Known CVE: Dependencies with known vulnerabilities
Tool: Apply SonarQube rule mappings from your Skills.

### Dimension 2: Performance Audit (性能影响)
Scan for:
- N+1 query patterns (ORM lazy loading traps)
- Missing database indexes for query conditions
- Synchronous calls that should be async
- Memory leak risks (unclosed streams, connection pools)
- Missing cache strategies for hot data
Complexity threshold: Cyclomatic complexity > 10 = WARNING.

### Dimension 3: Norms Audit (规范违例)
Scan for:
- Naming convention violations (refer to naming-conventions Skill)
- Missing or inadequate error handling
- Code duplication (DRY violations)
- Comment completeness for business logic
- Exception handling pattern compliance

## Output Format
For each issue found, output:

```json
{
  "severity": "BLOCKER|CRITICAL|MAJOR|MINOR",
  "dimension": "security|performance|norms",
  "file": "path/to/File.java",
  "line": 128,
  "title": "SQL Injection Risk",
  "description": "Direct string concatenation used to build SQL query.",
  "current_code": "String sql = \"SELECT * FROM orders WHERE id = \" + orderId;",
  "suggested_fix": "Use PreparedStatement with parameterized query.",
  "reference": "Security Coding Standard §3.2.1"
}
```

## Severity Escalation Rules
- BLOCKER → Block merge. Return to Coding Agent for fix.
- CRITICAL → Should fix before merge, or create tracking Issue.
- MAJOR → Auto-generate fix patch in separate commit [AutoFix by Review Agent].
- MINOR → Note in report only. Does not block merge.

## Behavioral Constraints
- ❌ Do NOT modify any source code directly (except MAJOR auto-fix patches)
- ❌ Do NOT approve your own fixes
- ❌ Do NOT skip any dimension of the audit
- ✅ Always provide actionable fix suggestions
- ✅ Always cite the specific standard being violated
- ✅ False positive rate must stay below 10%
```

### 4.4 Coding Agent Prompt

```markdown
# Coding Agent — 研发官 System Prompt

## Role Definition
You are a Coding Agent (研发官) of the SITC digital employee cluster.
You write production-quality business code strictly within the scope defined
by an approved Implementation Plan.

## Execution Protocol

### Input Requirements
Before writing any code, verify you have:
1. ✅ Approved Implementation_Plan.md (with "APPROVED" status)
2. ✅ Relevant OpenAPI contract YAML files
3. ✅ Domain Skills for the target module
4. ✅ Feature branch created and checked out

### Coding Rules
1. **Plan-Bound**: Only create/modify files listed in Implementation_Plan.md
2. **Contract-Aligned**: Interface signatures must match OpenAPI spec exactly
3. **Test-Friendly**: Generate unit test skeletons alongside business code
4. **Comment-WHY**: Add comments explaining WHY, not WHAT
5. **No Gold-Plating**: Implement exactly what the plan specifies, nothing more

### Self-Fix Loop (3-Strike Rule)
When Test Agent reports failures:

Loop iteration 1-3:
  1. Read test failure report (JSON format)
  2. Analyze root cause from error stack
  3. Apply targeted fix (minimal change)
  4. Re-submit for testing

If iteration > 3:
  → Set status to "needs_human"
  → Report: what was tried, what failed, suspected root cause
  → DO NOT attempt further autonomous fixes

### Code Quality Standards
- Cyclomatic complexity per method: ≤ 10
- Method length: ≤ 50 lines (prefer ≤ 30)
- Class length: ≤ 300 lines
- Test coverage for new code: ≥ 80%
- Zero compiler warnings
- Zero Lint violations

## Parallelism Rules
You may be one of 2-4 Coding Agents working in parallel.
- Stay strictly within your assigned file scope
- If you detect a potential conflict with another agent's scope,
  escalate to Orchestrator
- Never modify shared utility classes without explicit plan approval
```

### 4.5 Spec Agent Prompt

```markdown
# Spec Agent — 方案官 System Prompt

## Role Definition
You are the Spec Agent (方案官). You generate Implementation Plans that
replace traditional technical review meetings with async documentation.

## Output: Implementation_Plan.md

You MUST produce a plan in this exact structure:

```markdown
## Implementation Plan: [Task Title]

### Status: DRAFT (awaiting architect approval)

### Affected Files
| Action | File Path | Description |
|--------|-----------|-------------|
| CREATE | src/main/java/.../NewService.java | New service class |
| MODIFY | src/main/java/.../ExistingController.java | Add new endpoint |

### Interface Changes
- New: POST /api/v1/xxx (see contract diff below)
- Modified: GET /api/v1/yyy (added query param)

### OpenAPI Contract Diff
```yaml
# Before → After diff of OpenAPI spec
```

### Logic Flow
1. Validate input credentials
2. Query DB for user record
3. Verify password hash
4. Generate JWT token
5. Return response

### Risk Assessment
| Risk | Impact | Mitigation |
|------|--------|------------|
| Backward compatibility | Medium | Version API endpoint |

### Estimated Agent Effort
- Coding Agent: ~2 hours
- Test Agent: ~1.5 hours
```

## Contract-First Constraint
**No code is written until this Plan is APPROVED.**
The Implementation Plan is the gate between planning and execution.

## Behavioral Constraints
- ❌ NEVER generate implementation code
- ❌ NEVER skip interface contract definition
- ❌ NEVER auto-approve your own plans
- ✅ Always include OpenAPI diff for any interface change
- ✅ Always assess backward compatibility risks
```

### 4.6 Orchestrator Agent Prompt

```markdown
# Orchestrator Agent — 编排官 System Prompt

## Role Definition
You are the Orchestrator Agent (编排官), the entry point and coordinator
of the digital employee cluster. You parse Jira Issues into executable
task chains (DAG).

## Input: Jira Issue with #Agent-Task tag
Required fields:
- INPUT: Current state / context
- OUTPUT: Expected deliverable
- DoD: Definition of Done (acceptance criteria)

## Output: Task Chain DAG (JSON)

```json
{
  "task_chain_id": "TC-2025-0042",
  "source_issue": "TRADE-1234",
  "created_at": "2025-02-20T10:00:00Z",
  "tasks": [
    {
      "task_id": "T1",
      "agent_role": "spec-agent",
      "description": "Generate implementation plan for login API",
      "depends_on": [],
      "timeout_minutes": 60,
      "priority": "high",
      "skills_required": ["L2-auth", "L1-security-coding"],
      "human_approval_required": true
    },
    {
      "task_id": "T2",
      "agent_role": "coding-agent",
      "description": "Implement login API per approved plan",
      "depends_on": ["T1"],
      "timeout_minutes": 180,
      "priority": "high",
      "skills_required": ["L2-auth", "L1-code-standards"],
      "human_approval_required": false
    }
  ]
}
```

## Escalation Rules
Flag as `Architect-Review-Needed` when:
- Module boundaries are ambiguous
- Task requires Skills not in the current library
- Circular dependencies detected in the DAG
- Cross-platform integration involved (JD/Douyin/Mini-Program)
```

### 4.7 Test Agent / Smoke Agent / Doc Agent（简略版）

```markdown
# Test Agent — 关键约束
- 三层测试：Unit → Contract → Scenario
- Contract Test 基于 OpenAPI YAML 自动生成
- Any contract deviation = BLOCKER (Immediate Stop)
- 失败报告必须包含：failedTest, expectedBehavior, actualBehavior,
  errorStack, suggestedFix, severity

# Smoke Agent — 关键约束
- 核心链路覆盖率必须 100%（JD/Douyin/小程序）
- 使用 Mock Server 模拟：正常200ms/超时3s/错误4xx-5xx/重复回调
- 同一链路失败3次 → 升级至平台整合工程师
- 触发时机：Review Agent通过后自动触发

# Doc Agent — 关键约束
- PR合并后自动触发（增量更新）
- 输出三件套：变更日志 + Wiki更新Draft + Skills增量PR
- 反熵机制：扫描90天未更新的Wiki页面 → 标记"待验证"
- Skills提炼：从Agent修复记录中提炼新规则
```

---

## 五、GitLab CI/CD Pipeline：Agent流水线集成

### 5.1 核心Pipeline定义

```yaml
# .gitlab-ci.yml — Agent-Integrated Pipeline

stages:
  - agent-validate      # 入口验证
  - agent-spec          # 方案生成
  - human-gate-spec     # 人工审批方案
  - agent-code          # 编码执行
  - agent-test          # 自动测试
  - agent-selffix       # 自修复循环
  - agent-review        # 三维审计
  - human-gate-review   # 人工确认审计
  - agent-smoke         # 端到端验证
  - human-gate-final    # 最终签收
  - agent-doc           # 文档沉淀（异步）
  - metrics             # KPI数据采集

# ============================================
# Stage 1: 入口验证
# ============================================
validate-issue:
  stage: agent-validate
  script:
    - |
      echo "Validating issue structure..."
      python3 scripts/validate_issue.py \
        --issue-key ${JIRA_ISSUE_KEY} \
        --check-input \
        --check-output \
        --check-dod \
        --check-agent-task-tag
  rules:
    - if: '$CI_PIPELINE_SOURCE == "trigger"'
      when: always

# ============================================
# Stage 2: 方案生成（Spec Agent）
# ============================================
generate-plan:
  stage: agent-spec
  script:
    - |
      docker run --rm \
        -v $(pwd)/skills:/skills:ro \
        -v $(pwd)/contracts:/contracts:ro \
        -v $(pwd):/workspace \
        -e AGENT_ROLE=spec-agent \
        -e TASK_CONTEXT="${TASK_CONTEXT}" \
        agent-sandbox:latest \
        claude --system-prompt /skills/agent-specific/spec-agent/prompt.md \
               --output /workspace/Implementation_Plan.md \
               "Generate implementation plan for: ${TASK_DESCRIPTION}"
    - |
      # 创建 Draft MR
      git checkout -b "plan/${JIRA_ISSUE_KEY}"
      git add Implementation_Plan.md
      git commit -m "chore: [Spec Agent] Implementation Plan for ${JIRA_ISSUE_KEY}"
      git push origin "plan/${JIRA_ISSUE_KEY}"
      gitlab-cli mr create \
        --title "[Plan] ${JIRA_ISSUE_KEY}" \
        --description "Auto-generated by Spec Agent. @architect please review." \
        --draft \
        --assignee ${ARCHITECT_USERNAME}
  artifacts:
    paths:
      - Implementation_Plan.md
    expire_in: 7 days

# ============================================
# Stage 3: 人工审批方案（门控）
# ============================================
approve-plan:
  stage: human-gate-spec
  script:
    - echo "Waiting for architect approval on MR..."
    - python3 scripts/wait_for_mr_approval.py --mr-iid ${MR_IID} --timeout 86400
  when: manual
  allow_failure: false

# ============================================
# Stage 4: 编码执行（Coding Agent × N 并行）
# ============================================
coding-module-1:
  stage: agent-code
  script:
    - |
      docker run --rm \
        -v $(pwd)/skills:/skills:ro \
        -v $(pwd)/contracts:/contracts:ro \
        -v $(pwd):/workspace \
        -e AGENT_ROLE=coding-agent \
        -e AGENT_ID=coding-01 \
        -e PLAN_FILE=/workspace/Implementation_Plan.md \
        agent-sandbox:latest \
        claude --system-prompt /skills/agent-specific/coding-agent/prompt.md \
               "Implement module 1 according to the approved plan"
  artifacts:
    paths:
      - src/
    expire_in: 1 day
  needs: ["approve-plan"]

coding-module-2:
  stage: agent-code
  script:
    - |
      # 第二个并行 Coding Agent（如果Plan拆分了多模块）
      docker run --rm \
        -v $(pwd):/workspace \
        -e AGENT_ROLE=coding-agent \
        -e AGENT_ID=coding-02 \
        agent-sandbox:latest \
        claude --system-prompt /skills/agent-specific/coding-agent/prompt.md \
               "Implement module 2 according to the approved plan"
  needs: ["approve-plan"]
  rules:
    - if: '$PARALLEL_MODULES == "true"'

# ============================================
# Stage 5: 自动测试（Test Agent）
# ============================================
run-tests:
  stage: agent-test
  script:
    - |
      docker run --rm \
        -v $(pwd):/workspace \
        -e AGENT_ROLE=test-agent \
        agent-sandbox:latest \
        claude --system-prompt /skills/agent-specific/test-agent/prompt.md \
               "Generate and execute tests for the code changes"
    - |
      # 契约测试（硬门控）
      redocly lint contracts/*.yaml --format=json > contract-lint.json
      python3 scripts/contract_test.py \
        --contract contracts/api.yaml \
        --code src/ \
        --strict
  artifacts:
    paths:
      - test-results/
      - coverage-report/
      - contract-lint.json
    reports:
      junit: test-results/junit.xml
      coverage_report:
        coverage_format: cobertura
        path: coverage-report/cobertura.xml

# ============================================
# Stage 6: 自修复循环（最多3轮）
# ============================================
selffix-loop:
  stage: agent-selffix
  script:
    - |
      MAX_LOOPS=3
      LOOP=0
      while [ $LOOP -lt $MAX_LOOPS ]; do
        # 检查测试结果
        if python3 scripts/check_test_results.py --results test-results/; then
          echo "All tests passed at loop $LOOP"
          exit 0
        fi

        LOOP=$((LOOP + 1))
        echo "Self-fix loop $LOOP of $MAX_LOOPS"

        # Coding Agent 分析失败并修复
        docker run --rm \
          -v $(pwd):/workspace \
          -e AGENT_ROLE=coding-agent \
          -e FIX_LOOP=$LOOP \
          agent-sandbox:latest \
          claude --system-prompt /skills/agent-specific/coding-agent/prompt.md \
                 "Analyze test failures and apply fix. Loop $LOOP of $MAX_LOOPS.
                  Failure report: $(cat test-results/failures.json)"

        # 重新执行测试
        docker run --rm \
          -v $(pwd):/workspace \
          -e AGENT_ROLE=test-agent \
          agent-sandbox:latest \
          claude --system-prompt /skills/agent-specific/test-agent/prompt.md \
                 "Re-run tests after self-fix loop $LOOP"
      done

      # 3轮后仍失败
      echo "SELF-FIX EXHAUSTED after $MAX_LOOPS loops"
      python3 scripts/notify_slack.py \
        --channel "#agent-alerts" \
        --message "🚨 Human-Intervention-Needed: ${JIRA_ISSUE_KEY} - Self-fix exhausted after 3 loops"
      exit 1
  when: on_failure

# ============================================
# Stage 7: 三维审计（Review Agent）
# ============================================
code-review:
  stage: agent-review
  script:
    - |
      # 获取MR diff
      git diff origin/main...HEAD > mr-diff.patch

      # Review Agent 三维审计
      docker run --rm \
        -v $(pwd):/workspace \
        -e AGENT_ROLE=review-agent \
        agent-sandbox:latest \
        claude --system-prompt /skills/agent-specific/review-agent/prompt.md \
               "Perform 3D audit on this code change.
                Diff: $(cat mr-diff.patch)
                Coverage: $(cat coverage-report/summary.json)"
    - |
      # 解析Review结果
      python3 scripts/parse_review.py \
        --report review-report.json \
        --fail-on-blocker \
        --post-to-mr ${MR_IID}
  artifacts:
    paths:
      - review-report.json

# ============================================
# Stage 8: 人工确认审计（门控）
# ============================================
confirm-review:
  stage: human-gate-review
  script:
    - echo "Review report posted to MR. Architect confirms all BLOCKERs resolved."
    - python3 scripts/wait_for_review_confirm.py --mr-iid ${MR_IID}
  when: manual

# ============================================
# Stage 9: 端到端验证（Smoke Agent）
# ============================================
smoke-test:
  stage: agent-smoke
  script:
    - |
      docker run --rm \
        -v $(pwd):/workspace \
        -v $(pwd)/skills/agent-specific/smoke-agent:/skills:ro \
        -e AGENT_ROLE=smoke-agent \
        -e STAGING_URL=${STAGING_URL} \
        agent-sandbox:latest \
        claude --system-prompt /skills/agent-specific/smoke-agent/prompt.md \
               "Execute smoke tests against staging environment"
  artifacts:
    paths:
      - smoke-report.json

# ============================================
# Stage 10: 最终签收（门控 → 人工One-Click Merge）
# ============================================
final-signoff:
  stage: human-gate-final
  script:
    - |
      echo "=== SIGN-OFF PACKAGE ==="
      echo "1. Test Coverage: $(cat coverage-report/summary.json | jq '.line_rate')"
      echo "2. Review Score:  $(cat review-report.json | jq '.overall_score')"
      echo "3. Smoke Status:  $(cat smoke-report.json | jq '.status')"
      echo "========================"
      echo "Waiting for architect to merge..."
  when: manual

# ============================================
# Stage 11: 文档沉淀（异步，不阻塞主流程）
# ============================================
generate-docs:
  stage: agent-doc
  script:
    - |
      docker run --rm \
        -v $(pwd):/workspace \
        -e AGENT_ROLE=doc-agent \
        agent-sandbox:latest \
        claude --system-prompt /skills/agent-specific/doc-agent/prompt.md \
               "Generate changelog, wiki update, and skills extraction
                for merged PR on ${JIRA_ISSUE_KEY}"
    - |
      # 提交 Skills 增量 PR
      git checkout -b "skills-update/${JIRA_ISSUE_KEY}"
      git add skills/
      git commit -m "chore: [Doc Agent] Skills update from ${JIRA_ISSUE_KEY}"
      git push origin "skills-update/${JIRA_ISSUE_KEY}"
  when: on_success
  allow_failure: true  # 不阻塞主流程

# ============================================
# Stage 12: KPI数据采集
# ============================================
collect-metrics:
  stage: metrics
  script:
    - |
      python3 scripts/collect_kpi.py \
        --task-id ${JIRA_ISSUE_KEY} \
        --pipeline-id ${CI_PIPELINE_ID} \
        --fpr $(cat test-results/fpr.json) \
        --selffix-loops $(cat selffix-counter.txt || echo 0) \
        --human-touch-time $(cat human-touch-log.json) \
        --token-usage $(cat token-usage.json) \
        --push-to-prometheus
  when: always
```

### 5.2 Contract-First 门控配置

```yaml
# .redocly.yaml — OpenAPI契约校验配置

extends:
  - recommended

rules:
  # 严格模式：任何偏差都是错误
  no-undefined-server-variable: error
  no-empty-servers: error
  operation-operationId: error
  operation-summary: error

  # 命名规范
  path-segment-plural: error
  naming-convention:
    severity: error
    options:
      pathItem: kebab-case
      parameter: camelCase
      property: camelCase

  # 安全要求
  security-defined: error

  # 响应规范
  response-contains-header:
    severity: error
    options:
      names:
        - X-Request-Id

  # 版本控制
  info-contact: error

theme:
  openapi: {}
```

---

## 六、Jira → Agent → GitLab 自动化链路

### 6.1 Jira Webhook 配置

```json
// Jira Webhook Configuration
{
  "name": "Agent-Task-Trigger",
  "url": "https://agent-orchestrator.internal.starbucks.com/api/v1/webhook/jira",
  "events": [
    "jira:issue_updated"
  ],
  "filters": {
    "issue-related-events-section": {
      "jql": "labels = \"Agent-Task\" AND status CHANGED TO \"Ready for Agent\""
    }
  },
  "excludeBody": false
}
```

### 6.2 Orchestrator Webhook Handler

```python
# orchestrator/webhook_handler.py

from flask import Flask, request, jsonify
import json
import subprocess

app = Flask(__name__)

@app.route('/api/v1/webhook/jira', methods=['POST'])
def handle_jira_webhook():
    """Jira Issue → Orchestrator Agent → GitLab Pipeline Trigger"""

    payload = request.json
    issue = payload.get('issue', {})

    # 1. 验证Issue三要素
    fields = issue.get('fields', {})
    description = fields.get('description', '')

    validation = validate_issue_structure(description)
    if not validation['valid']:
        notify_slack(
            channel='#agent-alerts',
            message=f"⚠️ Issue {issue['key']} 缺少必要字段: {validation['missing']}"
        )
        return jsonify({'status': 'rejected', 'reason': validation['missing']}), 400

    # 2. 提取任务上下文
    task_context = {
        'issue_key': issue['key'],
        'title': fields.get('summary'),
        'input': extract_section(description, 'INPUT'),
        'output': extract_section(description, 'OUTPUT'),
        'dod': extract_section(description, 'DoD'),
        'labels': [l['name'] for l in fields.get('labels', [])],
        'priority': fields.get('priority', {}).get('name'),
    }

    # 3. 识别涉及的模块 → 选择Skills
    modules = identify_modules(task_context)
    required_skills = select_skills(modules)

    # 4. 触发 GitLab Pipeline
    trigger_result = trigger_gitlab_pipeline(
        project_id=GITLAB_PROJECT_ID,
        ref='main',
        variables={
            'JIRA_ISSUE_KEY': task_context['issue_key'],
            'TASK_DESCRIPTION': task_context['title'],
            'TASK_CONTEXT': json.dumps(task_context),
            'REQUIRED_SKILLS': json.dumps(required_skills),
            'PARALLEL_MODULES': str(len(modules) > 1).lower(),
        }
    )

    # 5. 更新Jira状态
    update_jira_status(issue['key'], 'In Agent Processing')

    return jsonify({
        'status': 'triggered',
        'pipeline_id': trigger_result['id'],
        'modules': modules,
        'skills': required_skills,
    })


def validate_issue_structure(description: str) -> dict:
    """校验Issue是否包含 INPUT/OUTPUT/DoD 三要素"""
    required = ['INPUT', 'OUTPUT', 'DoD']
    missing = [r for r in required if r not in description.upper()]
    return {'valid': len(missing) == 0, 'missing': missing}


def identify_modules(context: dict) -> list:
    """根据Issue内容识别涉及的业务模块"""
    module_keywords = {
        'redemption': ['核销', 'redeem', '兑换', '扣减'],
        'benefits': ['权益', 'coupon', '优惠券', '礼品卡', 'gift card', 'stars'],
        'jd-adapter': ['京东', 'JD', 'jd.com'],
        'douyin-adapter': ['抖音', 'Douyin', 'douyin'],
        'miniprogram': ['小程序', 'miniprogram', 'mini program'],
        'bundling': ['搭售', 'bundle', '组合'],
        'marketing': ['营销', 'marketing', '活动配置'],
    }

    text = f"{context['title']} {context.get('input', '')} {context.get('output', '')}"
    detected = []
    for module, keywords in module_keywords.items():
        if any(kw.lower() in text.lower() for kw in keywords):
            detected.append(module)

    return detected if detected else ['general']


def select_skills(modules: list) -> list:
    """根据模块选择需要注入的Skills"""
    skills = ['L1-common/code-standards.md', 'L1-common/security-coding.md']

    module_skill_map = {
        'redemption': ['L2-domain/redemption/'],
        'benefits': ['L2-domain/benefits/'],
        'jd-adapter': ['L2-domain/platform-adapters/jd-integration.md'],
        'douyin-adapter': ['L2-domain/platform-adapters/douyin-integration.md'],
        'miniprogram': ['L2-domain/platform-adapters/miniprogram-integration.md'],
        'bundling': ['L2-domain/bundling/'],
        'marketing': ['L2-domain/marketing/'],
    }

    for module in modules:
        skills.extend(module_skill_map.get(module, []))

    # 总是注入当前Sprint上下文
    skills.append('L3-project/current-sprint/')

    return skills
```

### 6.3 完整链路状态流转

```
Jira Status Flow（与Agent Pipeline对应）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Jira Status              Pipeline Stage           触发条件
─────────               ──────────────           ────────
Open                     —                        人工创建Issue
  │
  ▼ (加 #Agent-Task 标签)
Ready for Agent          agent-validate           Jira Webhook
  │
  ▼
Agent Planning           agent-spec               Orchestrator启动
  │
  ▼
Awaiting Plan Approval   human-gate-spec          Spec Agent产出Plan
  │
  ▼ (架构师 Approved)
Agent Coding             agent-code               Plan审批通过
  │
  ├─▶ Agent Testing      agent-test               代码提交
  │
  ├─▶ Agent Self-Fixing  agent-selffix            测试失败时
  │
  ▼
Agent Reviewing          agent-review             测试通过
  │
  ▼
Awaiting Review Confirm  human-gate-review        Review报告产出
  │
  ▼ (架构师确认BLOCKER已解决)
Agent Smoke Testing      agent-smoke              Review确认
  │
  ▼
Awaiting Sign-off        human-gate-final         Smoke通过
  │
  ▼ (架构师 One-Click Merge)
Done                     agent-doc (async)         合并完成
```

---

## 七、KPI数据采集与Dashboard实现

### 7.1 Prometheus Metrics定义

```python
# metrics/kpi_exporter.py

from prometheus_client import Gauge, Counter, Histogram, start_http_server

# ============================================
# 产出效能指标
# ============================================
agent_fpr = Gauge(
    'agent_fpr_ratio',
    'First Pass Rate - 一次性通过测试+Review的比例',
    ['agent_role', 'module']
)

selffix_rate = Gauge(
    'agent_selffix_rate',
    'Self-Fix Rate - Agent独立修复Bug成功的比例',
    ['agent_role']
)

selffix_loops = Histogram(
    'agent_selffix_loops_total',
    'Self-fix loops consumed per task',
    ['agent_role'],
    buckets=[0, 1, 2, 3]
)

# ============================================
# 人力节省指标
# ============================================
human_touch_time = Histogram(
    'human_touch_time_minutes',
    'In-house员工单任务有效介入时长',
    ['task_type'],  # simple_config | standard | cross_module | cross_platform
    buckets=[5, 10, 15, 30, 45, 60, 90, 120, 180]
)

# ============================================
# 成本指标
# ============================================
token_usage = Counter(
    'agent_token_usage_total',
    'Total tokens consumed',
    ['agent_role', 'token_type']  # input_tokens, output_tokens
)

token_cost_usd = Gauge(
    'agent_token_cost_usd',
    'Token cost in USD per task',
    ['agent_role']
)

token_vs_psp = Gauge(
    'token_vs_psp_ratio',
    'Token费用 vs 同等PSP服务费的比率',
    []
)

# ============================================
# 质量安全指标
# ============================================
contract_deviation = Gauge(
    'contract_deviation_count',
    'Number of contract deviations detected',
    ['agent_role']
)

review_blocker_count = Counter(
    'review_blocker_total',
    'Total BLOCKER issues found in review',
    ['dimension']  # security, performance, norms
)

review_false_positive = Gauge(
    'review_false_positive_rate',
    'Review误报率',
    []
)

smoke_pass_rate = Gauge(
    'smoke_pass_rate',
    'Smoke首次通过率',
    ['platform']  # jd, douyin, miniprogram
)

# ============================================
# 知识资产指标
# ============================================
skills_count = Gauge(
    'skills_total_count',
    'Total number of active skills',
    ['layer']  # L1, L2, L3
)

skills_monthly_additions = Gauge(
    'skills_monthly_additions',
    'Monthly new skills added',
    []
)

# ============================================
# 运行状态指标
# ============================================
agent_task_duration = Histogram(
    'agent_task_duration_seconds',
    'Agent task execution duration',
    ['agent_role', 'status'],  # success, failure, needs_human
    buckets=[60, 300, 600, 1800, 3600, 7200, 14400]
)

pipeline_total = Counter(
    'pipeline_runs_total',
    'Total pipeline runs',
    ['status']  # success, failed, cancelled
)

human_intervention_count = Counter(
    'human_intervention_total',
    'Total human interventions triggered',
    ['reason']  # selffix_exhausted, blocker_timeout, smoke_failure, security_event
)
```

### 7.2 Grafana Dashboard JSON模板

```json
{
  "dashboard": {
    "title": "SITC Agent Cluster Command Center",
    "panels": [
      {
        "title": "Agent-FPR (First Pass Rate)",
        "type": "gauge",
        "targets": [{"expr": "agent_fpr_ratio{agent_role='coding-agent'}"}],
        "fieldConfig": {
          "defaults": {
            "thresholds": {
              "steps": [
                {"color": "red", "value": 0},
                {"color": "orange", "value": 60},
                {"color": "green", "value": 80}
              ]
            },
            "max": 100,
            "unit": "percent"
          }
        }
      },
      {
        "title": "Self-Fix Rate",
        "type": "gauge",
        "targets": [{"expr": "agent_selffix_rate{agent_role='coding-agent'}"}],
        "fieldConfig": {
          "defaults": {
            "thresholds": {
              "steps": [
                {"color": "red", "value": 0},
                {"color": "orange", "value": 70},
                {"color": "green", "value": 85}
              ]
            }
          }
        }
      },
      {
        "title": "Token-vs-PSP Cost Ratio",
        "type": "stat",
        "targets": [{"expr": "token_vs_psp_ratio"}],
        "fieldConfig": {
          "defaults": {
            "thresholds": {
              "steps": [
                {"color": "green", "value": 0},
                {"color": "orange", "value": 10},
                {"color": "red", "value": 15}
              ]
            },
            "unit": "percent"
          }
        }
      },
      {
        "title": "Contract Deviation",
        "type": "stat",
        "targets": [{"expr": "contract_deviation_count"}],
        "fieldConfig": {
          "defaults": {
            "thresholds": {
              "steps": [
                {"color": "green", "value": 0},
                {"color": "red", "value": 1}
              ]
            }
          }
        }
      },
      {
        "title": "Human-Touch Time Distribution",
        "type": "histogram",
        "targets": [{"expr": "human_touch_time_minutes_bucket"}]
      },
      {
        "title": "Task Duration by Agent Role",
        "type": "timeseries",
        "targets": [
          {"expr": "rate(agent_task_duration_seconds_sum[1h]) / rate(agent_task_duration_seconds_count[1h])", "legendFormat": "{{agent_role}}"}
        ]
      },
      {
        "title": "Human Interventions (24h)",
        "type": "table",
        "targets": [{"expr": "increase(human_intervention_total[24h])"}]
      },
      {
        "title": "Skills Library Health",
        "type": "bargauge",
        "targets": [{"expr": "skills_total_count", "legendFormat": "{{layer}}"}]
      }
    ]
  }
}
```

### 7.3 Slack告警规则

```yaml
# alertmanager/rules.yml

groups:
  - name: agent-cluster-alerts
    rules:
      # FPR 连续低于预警线
      - alert: AgentFPRCritical
        expr: agent_fpr_ratio{agent_role="coding-agent"} < 60
        for: 336h  # 2 weeks
        labels:
          severity: critical
        annotations:
          summary: "Agent FPR below warning line for 2 weeks"
          description: "Coding Agent FPR is {{ $value }}%. Trigger offboarding review."
          action: "进入准出流程：优化Prompt → 重测 → 若仍不达标则替换实例"

      # 契约偏差（零容忍）
      - alert: ContractDeviation
        expr: contract_deviation_count > 0
        for: 0m
        labels:
          severity: blocker
        annotations:
          summary: "🚨 Contract Deviation Detected - IMMEDIATE STOP"
          description: "Contract deviation count: {{ $value }}. Pipeline must be blocked."

      # Token成本超标
      - alert: TokenCostOverBudget
        expr: token_vs_psp_ratio > 15
        for: 168h  # 1 week
        labels:
          severity: warning
        annotations:
          summary: "Token-vs-PSP ratio exceeds 15%"
          description: "Current ratio: {{ $value }}%. Review token optimization strategy."

      # 人工介入频率过高
      - alert: HighHumanIntervention
        expr: increase(human_intervention_total[168h]) > 10
        labels:
          severity: warning
        annotations:
          summary: "High human intervention frequency"
          description: "{{ $value }} human interventions in past week. Review Skills coverage."

      # Self-Fix 耗尽
      - alert: SelfFixExhausted
        expr: increase(human_intervention_total{reason="selffix_exhausted"}[24h]) > 0
        labels:
          severity: critical
        annotations:
          summary: "🚨 Self-fix exhausted - Human Intervention Needed"
```

---

## 八、Phase 1 执行手册：Review Agent首发

### 8.1 Phase 1 目标（与汇报"下一步行动计划"完全对齐）

```
汇报 Next Steps              本方案落地任务
─────────────              ───────────────
01 Initiate Review Agent   → 8.2 Review Agent GitLab CI集成
02 Setup GitLab CI/CD      → 8.3 Pipeline模板部署
03 Prompt Engineering      → 8.4 Implementation_Plan.md 模板开发
```

### 8.2 Review Agent GitLab CI集成（Week 1-2）

**第1天：环境准备**

```bash
# 1. 创建 Skills 仓库
git init skills-repo
cd skills-repo
mkdir -p L1-common agent-specific/review-agent

# 2. 部署公共规范 Skills
# 将团队现有的代码规范、安全编码规范转化为 Markdown Skill 文件
cp /path/to/company-code-standards.docx L1-common/code-standards.md
cp /path/to/security-coding-guide.docx L1-common/security-coding.md

# 3. 部署 Review Agent 专属 Skills
# 导入 SonarQube 规则集
python3 scripts/sonarqube_to_skill.py \
  --sonar-url ${SONARQUBE_URL} \
  --output agent-specific/review-agent/sonarqube-rules.md

# 导入历史Bug模式库
python3 scripts/extract_bug_patterns.py \
  --jira-project TRADE \
  --since 2024-01-01 \
  --output agent-specific/review-agent/bug-pattern-library.md
```

**第2-3天：Review Agent接入非核心仓库**

```yaml
# .gitlab-ci.yml 增量（追加到现有Pipeline）

agent-review:
  stage: review  # 加在现有 test stage 之后
  image: agent-sandbox:latest
  script:
    - |
      # 获取MR变更
      git diff ${CI_MERGE_REQUEST_DIFF_BASE_SHA}...${CI_COMMIT_SHA} > changes.patch

      # Review Agent 执行三维审计
      claude --system-prompt /skills/agent-specific/review-agent/prompt.md \
             --max-tokens 8000 \
             "Perform 3D audit on these changes: $(cat changes.patch)"

      # 将结果发布为MR评论
      python3 scripts/post_review_to_mr.py \
        --report review-report.json \
        --mr-iid ${CI_MERGE_REQUEST_IID}
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
  allow_failure: true  # Phase 1 不阻断，仅作建议
```

**第4-10天：Shadow Mode运行 & 数据收集**

```
Shadow Mode 运行规则：
━━━━━━━━━━━━━━━━━━━

1. Review Agent 对所有 MR 运行三维审计
2. 但结果只作为 MR 评论展示，不阻断合并
3. 人工 Reviewer 照常 Review，不改变现有流程
4. 同时收集以下对比数据：

收集项                      数据来源
────────                  ─────────
Agent发现的问题数量         review-report.json
人工发现的问题数量          GitLab MR comments (人工)
Agent误报数量              人工标注 (👎 reaction on agent comment)
Agent漏报数量              人工发现但Agent未标记的
Agent Review耗时           Pipeline job duration
人工Review耗时             Jira工时记录
```

### 8.3 Phase 1 Go/No-Go检查点

```
Week 2 检查点：
━━━━━━━━━━━━━

✅ Go 标准（全部满足）：
  □ Review Agent 覆盖 100% MR（自动触发，无遗漏）
  □ 误报率 < 20%（通过人工标注统计）
  □ Review 耗时 < 10分钟/PR（Pipeline数据）
  □ BLOCKER级问题发现率 ≥ 80%（对比人工Review）
  □ 无安全事件（Agent未产生任何越权操作）

✗ No-Go 处理：
  □ 误报率 > 20% → 优化Skills（增加bug-pattern-library）
  □ 漏报严重 → 检查Prompt是否涵盖三维度
  □ 耗时 > 10分钟 → 优化patch输入，减少无关上下文
```

### 8.4 Implementation_Plan.md 模板开发（Week 2-3）

这是 Spec Agent 产出的核心制品，也是 "Contract-First Constraint" 的物化载体：

```markdown
<!-- Template: implementation-plan-template.md -->
<!-- Spec Agent 按此模板生成方案，人工架构师审批此文档 -->

## Implementation Plan: {{TASK_TITLE}}

> **Source Issue**: {{JIRA_ISSUE_KEY}}
> **Generated by**: Spec Agent ({{AGENT_ID}})
> **Generated at**: {{TIMESTAMP}}
> **Status**: 🟡 DRAFT — Awaiting Architect Approval

---

### 1. Task Summary
{{一句话描述任务目标}}

### 2. Affected Files

| Action | File Path | Description | Estimated Lines |
|--------|-----------|-------------|----------------|
| CREATE | `src/main/java/com/starbucks/trading/...` | ... | ~50 |
| MODIFY | `src/main/java/com/starbucks/trading/...` | ... | ~20 |
| DELETE | — | — | — |

### 3. Interface Changes (Contract Diff)

#### New Endpoints
```yaml
# OpenAPI 3.0 YAML for new endpoints
paths:
  /api/v1/xxx:
    post:
      operationId: createXxx
      summary: ...
      requestBody:
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/XxxRequest'
      responses:
        '201':
          description: Created
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/XxxResponse'
```

#### Modified Endpoints
```diff
# Before → After diff
- /api/v1/yyy:
-   get:
-     parameters: []
+ /api/v1/yyy:
+   get:
+     parameters:
+       - name: status
+         in: query
+         schema:
+           type: string
```

### 4. Logic Flow

```
Step 1: {{描述}}
  └── {{实现要点}}
Step 2: {{描述}}
  └── {{实现要点}}
...
```

### 5. Database Changes

| Change Type | Table/Index | Description | Reversible |
|-------------|-------------|-------------|-----------|
| ALTER TABLE | — | — | ✅/❌ |
| CREATE INDEX | — | — | ✅ |

### 6. Risk Assessment

| Risk | Probability | Impact | Mitigation |
|------|------------|--------|------------|
| Backward compatibility | Low/Med/High | ... | ... |
| Performance degradation | ... | ... | ... |

### 7. Test Strategy

| Test Layer | Coverage Target | Key Scenarios |
|-----------|----------------|---------------|
| Unit Test | ≥ 80% lines | {{列出关键Case}} |
| Contract Test | 100% | All new/modified endpoints |
| Scenario Test | ≥ 95% critical paths | {{列出关键业务场景}} |

### 8. Estimated Effort

| Agent Role | Estimated Time | Parallelizable |
|-----------|---------------|----------------|
| Coding Agent | ~X hours | Yes (if multi-module) |
| Test Agent | ~Y hours | Parallel with coding |
| Review Agent | ~10 min | Sequential |
| Smoke Agent | ~15 min | Sequential |

---

### Approval

- [ ] **Architect Review**: @{{ARCHITECT_USERNAME}}
- [ ] **Contract Alignment**: All interface changes reflected in OpenAPI YAML
- [ ] **Scope Clarity**: All affected files explicitly listed
- [ ] **Risk Acknowledged**: All identified risks have mitigations

> **APPROVED** / **REJECTED** (with feedback): ___________
> **Approver**: ___________
> **Date**: ___________
```

---

## 九、MVP复盘与Phase 2关键调整

### 9.1 营销配置接口MVP数据复盘

基于汇报中披露的MVP结果（Starbucks Marketing Configuration Interface）：

| 指标 | MVP实际数据 | v1.0目标 | Operations Manual目标 | 差距分析 |
|------|-----------|---------|---------------------|---------|
| 资源投入 | 1 Human + 3 Agents | — | — | 验证了最小配置可行性 |
| 交付时间 | 48小时 | — | — | 符合预期 |
| 对齐会议 | **0次** | 减少70% | — | 超出预期：完全消除 |
| 代码覆盖率 | **100%** | ≥ 80% | — | 超出预期 |
| Human-Touch | 估算 < 60min | < 90min | < 15min | Phase 2需进一步压缩 |

### 9.2 从MVP到Phase 2的关键差距

```
MVP验证了什么：
  ✅ Agent可以在48h内完成端到端研发循环
  ✅ Zero alignment meetings 可行
  ✅ 100% Code Coverage 可达成
  ✅ 1 Human + 3 Agents 的最小单元可运行

MVP未覆盖的（Phase 2 必须解决）：
  ❓ 多模块并行（MVP只有1个模块）
  ❓ 跨平台集成（MVP未涉及JD/Douyin）
  ❓ 自修复循环的实际成功率
  ❓ Skills自动沉淀的闭环
  ❓ Token成本的精确核算
  ❓ Human-Touch从60min压缩到15min的路径
```

### 9.3 Human-Touch < 15min 的实现路径

Operations Manual 提出了比设计方案v1.0更激进的 Human-Touch < 15 min/task 目标。实现路径：

```
当前 ~60min 的分解：
  P0 创建Issue (30min)        → 自动化方向：Issue模板 + Jira自动化
  P2 审批方案 (15min)         → 优化方向：Spec Agent方案质量提升→架构师只需扫一眼
  P5 确认Review (10min)       → 优化方向：Review Agent误报降低→BLOCKER自动分类
  P7 最终签收 (20min)         → 优化方向：三份报告合一Dashboard→一键审批

压缩到 15min 的方案：
━━━━━━━━━━━━━━━━━━━

Phase 2a: Issue模板标准化（30min → 5min）
  - 预制Issue模板：标准功能类/配置类/跨模块类
  - Jira Automation：自动填充INPUT/OUTPUT/DoD的框架
  - 架构师只需填写核心业务逻辑描述

Phase 2b: 方案审批简化（15min → 3min）
  - Spec Agent 方案质量达到 ≥90% 一次通过率
  - 低风险任务（简单配置类）→ 自动审批（无需人工）
  - 标准任务 → 架构师只看 Contract Diff 部分

Phase 2c: 签收Dashboard化（30min → 5min）
  - 三份报告合并为单页Dashboard视图
  - 绿灯（全部通过）→ One-Click Merge
  - 黄灯（有WARNING）→ 展开查看 → 确认/驳回
  - 红灯（有BLOCKER）→ 自动阻断，无需人工操作

Phase 2d: 标准任务自动流转（目标态）
  - 简单配置类任务：Issue → Agent全自动 → Dashboard绿灯 → 架构师一键签收
  - 目标：Human-Touch ≤ 5min（创建Issue 3min + 签收 2min）
```

### 9.4 Token成本优化策略（达成 < 10%）

```
Token成本的三大消耗来源：
  1. Context注入（Skills + 代码上下文） → 占总Token 40%
  2. 代码生成（Coding Agent） → 占总Token 35%
  3. 自修复循环（失败重试） → 占总Token 25%

优化策略：
━━━━━━━━

策略1：Skills精准注入（减少40% → 25%）
  - 不注入全量Skills，只注入任务相关的
  - 使用 Skills Router 按模块精准匹配
  - L1公共Skills做摘要压缩（长文档→关键规则摘要）

策略2：增量编码（减少35% → 20%）
  - 提供精确的文件范围（不给全量代码库上下文）
  - 使用 git diff 而非全文件作为Review输入
  - Coding Agent 只看 Plan + 相关文件，不看无关代码

策略3：提高FPR减少自修复（减少25% → 10%）
  - FPR从80%提升到90% → 自修复循环减少50%
  - 关键手段：Skills质量提升（更好的边界Case覆盖）
  - 失败模式库积累（Self-Fix Pattern Skill持续更新）

预期效果：
  优化前：~¥25,000/月 Token费用（约PSP的10%）
  优化后：~¥15,000/月 Token费用（约PSP的6%）
  目标：Token-vs-PSP < 10% ✅
```

---

## 十、附录：配置文件与模板全集

### 10.1 文件清单

```
silicon-agent-infra/                    # 基础设施仓库
├── docker/
│   ├── Dockerfile.agent-sandbox        # Agent沙箱镜像 → 见第二章
│   ├── docker-compose.agent-cluster.yml# 编排文件 → 见第二章
│   └── network-policy.yml              # 网络隔离策略
│
├── gitlab-ci/
│   ├── .gitlab-ci.yml                  # 主Pipeline → 见第五章
│   ├── templates/
│   │   ├── agent-review-stage.yml      # Review Agent阶段模板
│   │   ├── agent-code-stage.yml        # Coding Agent阶段模板
│   │   └── agent-smoke-stage.yml       # Smoke Agent阶段模板
│   └── scripts/
│       ├── validate_issue.py           # Issue三要素校验
│       ├── contract_test.py            # 契约测试执行器
│       ├── parse_review.py             # Review报告解析
│       ├── post_review_to_mr.py        # 发布Review到MR评论
│       ├── check_test_results.py       # 测试结果检查
│       ├── notify_slack.py             # Slack通知
│       ├── collect_kpi.py              # KPI数据采集
│       └── wait_for_mr_approval.py     # MR审批等待
│
├── orchestrator/
│   ├── webhook_handler.py              # Jira Webhook处理器 → 见第六章
│   ├── skills_router.py                # Skills智能路由
│   └── dag_builder.py                  # 任务DAG构建器
│
├── monitoring/
│   ├── prometheus.yml                  # Prometheus配置
│   ├── alertmanager/
│   │   └── rules.yml                   # 告警规则 → 见第七章
│   └── grafana/
│       └── dashboards/
│           └── command-center.json     # Dashboard模板 → 见第七章
│
├── skills-repo/                        # Skills知识库 → 见第三章
│   ├── L1-common/
│   ├── L2-domain/
│   ├── L3-project/
│   ├── agent-specific/
│   └── meta/
│
├── contracts-repo/                     # OpenAPI契约库
│   ├── api/
│   │   ├── redemption.yaml
│   │   ├── benefits.yaml
│   │   ├── jd-adapter.yaml
│   │   └── douyin-adapter.yaml
│   └── .redocly.yaml                  # 契约校验配置 → 见第五章
│
└── templates/
    ├── jira-issue-template.md          # Jira Issue模板
    ├── implementation-plan-template.md # Implementation Plan模板 → 见第八章
    └── review-report-template.json     # Review报告模板
```

### 10.2 Jira Issue标准模板

```markdown
<!-- Jira Issue Template for #Agent-Task -->

## 任务标题
[简洁描述任务目标]

## Labels
`Agent-Task`, `[模块名]`, `[优先级]`

## INPUT（当前状态）
- 当前系统行为描述
- 相关API当前状态
- 已有数据结构

## OUTPUT（期望结果）
- 期望的新行为/接口/功能
- 期望的数据格式
- 期望的性能指标

## DoD（完成标准）
- [ ] 代码实现完成，通过所有测试
- [ ] Contract Test 100% 通过
- [ ] 行覆盖率 ≥ 80%
- [ ] Review Agent 审计无BLOCKER
- [ ] Smoke Test 通过（如涉及集成链路）
- [ ] 文档更新完成

## 补充上下文（可选）
- 相关设计文档链接
- 历史相关Issue
- 特殊注意事项
```

### 10.3 Phase实施检查清单

```
Phase 1 检查清单（Month 1-2）
━━━━━━━━━━━━━━━━━━━━━━━━━━

基础设施：
  □ Docker Agent Sandbox镜像构建并推送到内部Registry
  □ Skills仓库初始化，L1公共规范Skills就绪
  □ Review Agent专属Skills就绪（SonarQube规则+Bug模式库）
  □ Prometheus + Grafana部署，Dashboard模板导入
  □ Slack告警Webhook配置

CI/CD集成：
  □ GitLab CI增加 agent-review stage
  □ Review Agent对所有MR自动运行（Shadow Mode）
  □ Review结果发布为MR评论
  □ 误报标注机制建立（MR评论reaction）

数据收集：
  □ 4周Review Agent vs 人工Review对比数据
  □ 误报率统计 < 20%
  □ BLOCKER发现率统计 ≥ 80%

Go/No-Go决策：
  □ 全部满足 → 进入Phase 2
  □ 部分不满足 → 优化Skills后延长2周Shadow

━━━━━━━━━━━━━━━━━━━━━━━━━━
Phase 2 检查清单（Month 3-4）
━━━━━━━━━━━━━━━━━━━━━━━━━━

新增Agent接入：
  □ Orchestrator Agent接入Jira Webhook
  □ Spec Agent产出Implementation Plan
  □ Coding Agent × 2 并行编码
  □ Test Agent自动测试 + 自修复循环
  □ 完整Pipeline端到端运行

Skills扩展：
  □ L2领域Skills就绪（核销/权益金/搭售）
  □ L3项目Skills机制建立
  □ Skills Router按模块精准注入

验证目标：
  □ 首个端到端任务完成，Human-Touch < 90min
  □ FPR ≥ 70%（首批达标即可）
  □ Contract-First流程在至少1个模块全面推行
  □ PSP对比数据优势明确

━━━━━━━━━━━━━━━━━━━━━━━━━━
Phase 3 检查清单（Month 5-6）
━━━━━━━━━━━━━━━━━━━━━━━━━━

全角色上线：
  □ Smoke Agent接入（Mock Server就绪）
  □ Doc Agent接入（Wiki集成就绪）
  □ 全7角色Pipeline完整运行

KPI达标：
  □ Token-vs-PSP < 10%
  □ Agent-FPR ≥ 80%
  □ Self-Fix Rate ≥ 85%
  □ Skills库 ≥ 30条有效Skill
  □ 月增量 ≥ 5条

组织就绪：
  □ 全部In-house员工完成角色转型培训
  □ PSP减配计划已执行
  □ 团队满意度 ≥ 80%
  □ 季度复评机制建立
```

---

> **文档版本**：v1.0
> **编制**：SITC Trading Team · Architecture Group
> **面向对象**：架构师 / Tech Lead / DevOps
> **密级**：内部技术文档
> **关联文档**：
> - 《数字员工集群完整设计方案 v1.0》— 角色定义 & KPI标准
> - 《硅基Agent战略演进白皮书 v1.0》— 战略论证 & ROI分析
> - 《数字员工集群运作全书 (Operations Manual)》— SOP & 治理规范
> **下一步**：按 Phase 1 检查清单执行，Week 1 完成 Review Agent 接入
