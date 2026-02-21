"""Role-based system prompts and stage instruction templates for Agent Worker."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional


# ---------------------------------------------------------------------------
# System prompts per agent role
# ---------------------------------------------------------------------------

SYSTEM_PROMPTS: Dict[str, str] = {
    "orchestrator": (
        "你是一个项目编排Agent，负责解析用户需求、协调各阶段工作流程、"
        "以及最终签收验收。你需要将模糊需求转化为结构化的执行计划，"
        "并在签收阶段综合评估所有产出物的质量。"
    ),
    "spec": (
        "你是一个技术规格Agent，擅长将需求描述转化为详细的技术方案。"
        "你的输出应包含：接口设计、数据模型、技术选型、实现步骤和风险评估。"
        "请以结构化的Markdown格式输出。"
    ),
    "coding": (
        "你是一个代码生成Agent，擅长根据技术规格编写高质量代码。"
        "你需要遵循最佳实践，包括：清晰的代码结构、适当的错误处理、"
        "必要的注释、以及符合项目规范的命名约定。请输出完整可运行的代码。"
    ),
    "test": (
        "你是一个测试Agent，擅长编写全面的测试用例。"
        "你需要覆盖：正常路径、边界条件、异常处理和性能场景。"
        "请使用项目对应的测试框架，输出可直接运行的测试代码。"
    ),
    "review": (
        "你是一个代码审查Agent，负责对代码进行全面的质量审查。"
        "你需要检查：代码规范、安全漏洞（OWASP Top 10）、性能问题、"
        "可维护性和架构合理性。请按严重程度分级列出发现的问题。"
    ),
    "smoke": (
        "你是一个冒烟测试Agent，负责设计端到端的冒烟测试方案。"
        "你需要验证系统的核心功能路径是否正常工作，"
        "包括关键用户场景、API端点可用性和数据流完整性。"
    ),
    "doc": (
        "你是一个文档生成Agent，负责编写技术文档。"
        "你需要生成：API文档、使用说明、变更日志和架构说明。"
        "文档应清晰、准确、易于理解，面向开发者和使用者。"
    ),
}

# ---------------------------------------------------------------------------
# Stage-specific instruction templates
# ---------------------------------------------------------------------------

STAGE_INSTRUCTIONS: Dict[str, str] = {
    "parse": (
        "请解析以下任务需求，输出结构化的执行计划：\n"
        "1. 需求要点提炼\n"
        "2. 技术可行性初步评估\n"
        "3. 建议的实施步骤\n"
        "4. 预期产出物\n"
        "5. 潜在风险和依赖"
    ),
    "spec": (
        "请根据需求解析结果，编写详细的技术规格方案：\n"
        "1. 接口设计（输入/输出/协议）\n"
        "2. 数据模型设计\n"
        "3. 技术选型和理由\n"
        "4. 详细实现步骤\n"
        "5. 测试策略建议\n"
        "6. 风险评估和缓解措施"
    ),
    "approve": (
        "请审批以下技术方案，给出你的评估意见：\n"
        "1. 方案完整性评估\n"
        "2. 技术可行性确认\n"
        "3. 风险点识别\n"
        "4. 改进建议（如有）\n"
        "5. 最终结论：批准/需修改"
    ),
    "code": (
        "请根据技术规格方案，生成实现代码：\n"
        "1. 按照规格中的接口设计实现\n"
        "2. 包含必要的错误处理\n"
        "3. 遵循项目代码规范\n"
        "4. 添加关键逻辑的注释\n"
        "5. 列出修改的文件清单"
    ),
    "test": (
        "请为以下代码实现编写测试用例：\n"
        "1. 单元测试（覆盖核心逻辑）\n"
        "2. 边界条件测试\n"
        "3. 异常处理测试\n"
        "4. 测试覆盖率目标 ≥ 80%\n"
        "5. 模拟数据和fixtures"
    ),
    "review": (
        "请对以下代码进行全面审查：\n"
        "1. 代码规范检查\n"
        "2. 安全漏洞扫描（SQL注入、XSS等）\n"
        "3. 性能瓶颈识别\n"
        "4. 架构合理性评估\n"
        "5. 按严重程度（Critical/Major/Minor）分类列出问题"
    ),
    "smoke": (
        "请设计冒烟测试方案：\n"
        "1. 核心功能场景验证步骤\n"
        "2. API端点可用性检查\n"
        "3. 数据流完整性验证\n"
        "4. 预期结果和通过标准\n"
        "5. 测试环境要求"
    ),
    "doc": (
        "请生成以下技术文档：\n"
        "1. API接口文档（端点、参数、响应示例）\n"
        "2. 使用说明（快速开始、配置项）\n"
        "3. 变更日志（本次修改内容）\n"
        "4. 架构说明（如涉及架构变更）"
    ),
    "signoff": (
        "请综合评估所有阶段的产出物，进行最终签收：\n"
        "1. 各阶段产出物完整性检查\n"
        "2. 需求满足度评估\n"
        "3. 质量指标总结\n"
        "4. 遗留问题清单（如有）\n"
        "5. 最终签收结论"
    ),
}


@dataclass
class StageContext:
    """Context for building LLM messages for a stage execution."""
    task_title: str
    task_description: Optional[str]
    stage_name: str
    agent_role: str
    prior_outputs: List[Dict[str, str]]  # [{"stage": name, "output": text}, ...]


def build_messages(ctx: StageContext) -> List[dict]:
    """Build the message list for an LLM call based on stage context."""
    system_prompt = SYSTEM_PROMPTS.get(ctx.agent_role, SYSTEM_PROMPTS["orchestrator"])
    stage_instruction = STAGE_INSTRUCTIONS.get(ctx.stage_name, "请完成当前阶段的工作。")

    # Build the user message with task context and prior outputs
    parts: List[str] = []
    parts.append(f"## 任务\n**{ctx.task_title}**")
    if ctx.task_description:
        parts.append(f"\n{ctx.task_description}")

    if ctx.prior_outputs:
        parts.append("\n## 前序阶段产出")
        for po in ctx.prior_outputs:
            parts.append(f"\n### {po['stage']} 阶段输出\n{po['output']}")

    parts.append(f"\n## 当前阶段: {ctx.stage_name}\n{stage_instruction}")

    user_content = "\n".join(parts)

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]
