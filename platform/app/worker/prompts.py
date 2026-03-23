"""Role-based system prompts and stage instruction templates for Agent Worker."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

_EXECUTION_STAGE_NAMES = {"code", "coding", "test", "des encrypt"}
_EXECUTION_MEMORY_LIMIT = 320
_EXECUTION_REPO_HINT_LIMIT = 720
_EXECUTION_PRIOR_LIMITS = {
    "parse": 520,
    "approve": 520,
    "spec": 720,
    "review": 720,
    "doc": 720,
    "code": 960,
    "coding": 960,
    "test": 960,
    "signoff": 960,
}
_EXECUTION_PRIOR_MARKER = "\n...(前序阶段产出已截断)"
_REPO_SECTION_PATTERN = re.compile(r"^###\s+(?P<title>[^\n]+)\n", re.MULTILINE)


# ---------------------------------------------------------------------------
# Prompt file loader
# ---------------------------------------------------------------------------

_PROMPTS_DIR = Path(__file__).parent / "prompts"
def _load_prompt(filename: str, fallback: str = "") -> str:
    """Load a prompt from an external .md file, falling back to *fallback*."""
    path = _PROMPTS_DIR / filename
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return fallback


# ---------------------------------------------------------------------------
# System prompts per agent role
# ---------------------------------------------------------------------------

SYSTEM_PROMPTS: Dict[str, str] = {
    "orchestrator": _load_prompt(
        "system_orchestrator.md",
        "你是一个项目编排Agent，负责解析用户需求、协调各阶段工作流程、"
        "以及最终签收验收。你需要将模糊需求转化为结构化的执行计划，"
        "并在签收阶段综合评估所有产出物的质量。",
    ),
    "spec": _load_prompt(
        "system_spec.md",
        "你是一个技术规格Agent，擅长将需求描述转化为详细的技术方案。"
        "你的输出应包含：接口设计、数据模型、技术选型、实现步骤和风险评估。"
        "请以结构化的Markdown格式输出。",
    ),
    "coding": _load_prompt(
        "system_coding.md",
        "你是一个代码生成Agent，擅长根据技术规格编写高质量代码。"
        "你需要遵循最佳实践，包括：清晰的代码结构、适当的错误处理、"
        "必要的注释、以及符合项目规范的命名约定。请输出完整可运行的代码。",
    ),
    "test": _load_prompt(
        "system_test.md",
        "你是一个测试Agent，擅长编写全面的测试用例。"
        "你需要覆盖：正常路径、边界条件、异常处理和性能场景。"
        "请使用项目对应的测试框架，输出可直接运行的测试代码。",
    ),
    "review": _load_prompt(
        "system_review.md",
        "你是一个代码审查Agent，负责对代码进行全面的质量审查。"
        "你需要检查：代码规范、安全漏洞（OWASP Top 10）、性能问题、"
        "可维护性和架构合理性。请按严重程度分级列出发现的问题。",
    ),
    "smoke": _load_prompt(
        "system_smoke.md",
        "你是一个冒烟测试Agent，负责设计端到端的冒烟测试方案。"
        "你需要验证系统的核心功能路径是否正常工作，"
        "包括关键用户场景、API端点可用性和数据流完整性。",
    ),
    "doc": _load_prompt(
        "system_doc.md",
        "你是一个文档生成Agent，负责编写技术文档。"
        "你需要生成：API文档、使用说明、变更日志和架构说明。"
        "文档应清晰、准确、易于理解，面向开发者和使用者。",
    ),
    "dispatch issue": (
        "你是 GitHub Issue 分发 Agent，负责理解 Issue 内容并将任务分发给对应的执行 Agent。\n"
        "开始工作前，你必须先调用 `skill` 工具加载 `github_dispatch_issue` skill，然后严格按照 skill 内容执行。\n"
        "你只负责分析和分发，不直接修改任何代码。\n"
        "输出只包含纯 JSON，不要在 JSON 前后附加任何自然语言叙述或「发往下一阶段」的指令文本。\n"
    ),
    "des encrypt": (
        "你是安全加密 Agent，负责对数据库的某个字段进行安全加密改造，并在完成后将结果回帖到 GitHub Issue。\n"
        "开始工作前，你必须先调用 `skill` 工具分别加载 `des_encrypt` 和 `github_issue_feedback` 两个 skill，然后严格按顺序完成：\n"
        "1. **按照 `des_encrypt` skill 执行代码改造**：完成加密代码修改，提交并推送到远端新分支。\n"
        "2. **按照 `github_issue_feedback` skill 回帖**：Push 完成后，用 curl 将分支名和任务地址贴回原始 GitHub Issue。\n"
    ),
}

# ---------------------------------------------------------------------------
# Stage-specific instruction templates
# ---------------------------------------------------------------------------

STAGE_INSTRUCTIONS: Dict[str, str] = {
    "parse": _load_prompt(
        "stage_parse.md",
        "请解析以下任务需求，输出结构化的执行计划：\n"
        "1. 需求要点提炼\n"
        "2. 技术可行性初步评估\n"
        "3. 建议的实施步骤\n"
        "4. 预期产出物\n"
        "5. 潜在风险和依赖",
    ),
    "spec": _load_prompt(
        "stage_spec.md",
        "请根据需求解析结果，编写详细的技术规格方案：\n"
        "1. 接口设计（输入/输出/协议）\n"
        "2. 数据模型设计\n"
        "3. 技术选型和理由\n"
        "4. 详细实现步骤\n"
        "5. 测试策略建议\n"
        "6. 风险评估和缓解措施",
    ),
    "approve": _load_prompt(
        "stage_approve.md",
        "请审批以下技术方案，给出你的评估意见：\n"
        "1. 方案完整性评估\n"
        "2. 技术可行性确认\n"
        "3. 风险点识别\n"
        "4. 改进建议（如有）\n"
        "5. 最终结论：批准/需修改",
    ),
    "code": _load_prompt(
        "stage_code.md",
        "请根据技术规格方案，生成实现代码：\n"
        "1. 按照规格中的接口设计实现\n"
        "2. 包含必要的错误处理\n"
        "3. 遵循项目代码规范\n"
        "4. 添加关键逻辑的注释\n"
        "5. 列出修改的文件清单",
    ),
    "test": _load_prompt(
        "stage_test.md",
        "请为以下代码实现编写测试用例：\n"
        "1. 单元测试（覆盖核心逻辑）\n"
        "2. 边界条件测试\n"
        "3. 异常处理测试\n"
        "4. 测试覆盖率目标 ≥ 80%\n"
        "5. 模拟数据和fixtures",
    ),
    "review": _load_prompt(
        "stage_review.md",
        "请对以下代码进行全面审查：\n"
        "1. 代码规范检查\n"
        "2. 安全漏洞扫描（SQL注入、XSS等）\n"
        "3. 性能瓶颈识别\n"
        "4. 架构合理性评估\n"
        "5. 按严重程度（Critical/Major/Minor）分类列出问题",
    ),
    "smoke": _load_prompt(
        "stage_smoke.md",
        "请设计冒烟测试方案：\n"
        "1. 核心功能场景验证步骤\n"
        "2. API端点可用性检查\n"
        "3. 数据流完整性验证\n"
        "4. 预期结果和通过标准\n"
        "5. 测试环境要求",
    ),
    "doc": _load_prompt(
        "stage_doc.md",
        "请生成以下技术文档：\n"
        "1. API接口文档（端点、参数、响应示例）\n"
        "2. 使用说明（快速开始、配置项）\n"
        "3. 变更日志（本次修改内容）\n"
        "4. 架构说明（如涉及架构变更）",
    ),
    "signoff": _load_prompt(
        "stage_signoff.md",
        "请综合评估所有阶段的产出物，进行最终签收：\n"
        "1. 各阶段产出物完整性检查\n"
        "2. 需求满足度评估\n"
        "3. 质量指标总结\n"
        "4. 遗留问题清单（如有）\n"
        "5. 最终签收结论",
    ),
    "dispatch_issue": (
        "先调用 `skill` 工具加载 `github_dispatch_issue`，然后严格按照 skill 内容完成分发。\n"
        "只输出 skill 中定义的 JSON Schema 结构化结果（纯 JSON，无 markdown 代码块标记），"
        "不要在 JSON 前后附加自然语言总结或对下一阶段的指令。\n"
        "不得直接修改任何代码，只负责分析和分发。"
    ),
    "des encrypt": (
        "接手 dispatch issue 传来的上下文。\n"
        "**第一步（必须）**：立即连续调用两次 `skill` 工具，分别加载 `des_encrypt` 和 `github_issue_feedback`。\n"
        "然后按顺序完成：\n"
        "1. **Coding**：严格按照 `des_encrypt` skill 执行代码改造。目标仓库已在当前 workspace 根目录检出，"
        "直接在此读写、commit、push，不要 `git clone` 到子目录。"
        "提交前先执行 `git status --short` 确认改动在同一仓库。\n"
        "2. **回帖**：Push 完成后，严格按照 `github_issue_feedback` skill，用 curl 将分支名和任务地址回帖到原始 GitHub Issue。\n"
    ),
}


STAGE_GUARDRAILS: Dict[str, str] = {
    "code": _load_prompt(
        "guardrail_code.md",
        "只完成当前阶段，不要提前执行后续阶段任务。\n"
        "不要为了理解整个仓库而广泛探索，优先基于已知信息直接实现。\n"
        "只有在缺少关键实现信息时才少量补读文件；最多再检查 3 个关键文件或执行 1 次探索性目录命令，"
        "之后必须开始修改代码。\n"
        "你可以为了验证实现而运行必要命令，但目标必须是最小必要验证。\n"
        "不要提前生成最终签收/验收报告，也不要调用 signoff、review、smoke、e2e-test 等后续阶段能力。\n"
        "完成实现并简要总结本阶段改动后结束。",
    ),
    "test": _load_prompt(
        "guardrail_test.md",
        "只完成当前阶段，不要提前执行后续阶段任务。\n"
        "请聚焦当前任务直接相关的自动化测试与验证，优先最小、最相关、最快的验证路径。\n"
        "最多再补读 2 个关键文件、执行 2 条验证命令；超过后必须停止扩展并给出结论。\n"
        "如果验证命令失败，必须明确给出失败命令、关键报错和阻塞点；不要只根据代码阅读就判定测试通过。\n"
        "如果相关测试已经通过，且已满足验收标准，请立即停止。\n"
        "不要继续扩展额外类型的测试，例如 E2E、冒烟、性能或签收报告，除非任务明确要求。",
    ),
    "signoff": _load_prompt(
        "guardrail_signoff.md",
        "此阶段负责输出最终签收结果。\n"
        "请基于已有阶段产出进行总结和结论，不要再回头扩展实现或测试范围。\n"
        "前序阶段产出里可能包含中间态、自修复前描述或已过时结论；如果它们与当前阶段直接读取到的文件内容、"
        "命令结果或已完成阶段的最终产出冲突，请以当前阶段直接核验到的最新事实为准。\n"
        "只有在 signoff 阶段再次核验后仍然成立的问题，才可以写入遗留问题或影响最终结论；"
        "已经被后续修改修复的问题，应标记为已解决，不要继续当作遗留问题。\n"
        "优先复用 test 阶段已经完成的最终验证结果；除非存在明确缺口，不要重复安装依赖、重跑整套测试，"
        "也不要让宿主环境差异覆盖已在正确环境中验证通过的结论。",
    ),
    "des encrypt": (
        "只完成当前阶段，不要提前执行后续阶段任务。\n"
        "当前 task workspace 根目录已经是可提交的目标仓库，请直接在这里修改、提交和推送。\n"
        "禁止再次 `git clone` 到子目录，也不要把 read/write/edit/commit 分散到两个不同仓库路径。\n"
        "开始提交前必须先在当前 workspace 根目录执行 `git status --short`，确认改动出现在同一个仓库。\n"
        "如果 issue 已明确只处理 `phone` 等单一字段，请把改动限制在直接相关的实体、Mapper、必要支撑类和最小验证；不要顺手修改 logback、代码生成器、环境模板或其他无直接关联文件。\n"
        "如果没有产生 git 变更，不要伪造完成结果；必须继续定位原因或明确失败点。"
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
    compressed_outputs: Optional[List[Dict[str, str]]] = None  # sliding-window compressed
    project_memory: Optional[str] = None  # injected project memory text
    repo_context: Optional[str] = None  # injected repo context (tech stack + dir tree)
    preflight_summary: Optional[str] = None  # deterministic stage-local workspace scan summary
    # Smart retry: failure context from previous attempt (Ralph Loop V2 pattern)
    retry_context: Optional[Dict[str, str]] = None  # {"error": msg, "prior_output": text}
    # Phase 1.4: Custom instruction from template stage definition
    custom_instruction: Optional[str] = None
    # Phase 1.3: Gate rejection feedback context
    gate_rejection_context: Optional[Dict[str, str]] = None  # {"comment": ..., "retry": "2/3"}


def _clip_stage_context(value: Optional[str], *, limit: int, marker: str) -> Optional[str]:
    text = (value or "").strip()
    if not text:
        return None
    if len(text) <= limit:
        return text
    keep_len = max(0, limit - len(marker))
    return text[:keep_len].rstrip() + marker


def _is_execution_stage(stage_name: str) -> bool:
    return (stage_name or "").strip().lower() in _EXECUTION_STAGE_NAMES


def _extract_repo_section(repo_context: str, title: str) -> str:
    text = (repo_context or "").strip()
    if not text:
        return ""
    matches = list(_REPO_SECTION_PATTERN.finditer(text))
    for index, match in enumerate(matches):
        if match.group("title").strip() != title:
            continue
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        return text[start:end].strip()
    return ""


def _collect_tree_matches(
    tree_lines: list[str],
    *,
    predicates: tuple[str, ...],
    limit: int,
    require_file: bool = False,
) -> list[str]:
    matches: list[str] = []
    seen: set[str] = set()
    for raw in tree_lines:
        line = raw.strip()
        lowered = line.lower()
        if not line or line.startswith("...(目录树已截断)"):
            continue
        if require_file and "." not in line.rsplit("/", 1)[-1]:
            continue
        if not any(token in lowered for token in predicates):
            continue
        if line in seen:
            continue
        seen.add(line)
        matches.append(line)
        if len(matches) >= limit:
            break
    return matches


def _build_execution_repo_hint(repo_context: Optional[str]) -> Optional[str]:
    text = (repo_context or "").strip()
    if not text:
        return None

    tech_stack = _extract_repo_section(text, "技术栈")
    repo_tree = _extract_repo_section(text, "目录结构")
    repo_tree_lines = [line for line in repo_tree.splitlines() if line.strip()]

    build_files = _collect_tree_matches(
        repo_tree_lines,
        predicates=(
            "build.gradle",
            "build.gradle.kts",
            "pom.xml",
            "package.json",
            "pyproject.toml",
            "go.mod",
            "cargo.toml",
        ),
        limit=3,
        require_file=True,
    )
    source_roots = _collect_tree_matches(
        repo_tree_lines,
        predicates=("src/main", "app/", "app\\", "server/", "lib/", "internal/"),
        limit=3,
    )
    test_roots = _collect_tree_matches(
        repo_tree_lines,
        predicates=("src/test", "tests/", "__tests__", "spec/"),
        limit=3,
    )
    impl_refs = _collect_tree_matches(
        repo_tree_lines,
        predicates=("controller", "handler", "service", "api", "route", "response"),
        limit=2,
        require_file=True,
    )

    parts: list[str] = []
    if tech_stack:
        parts.append(f"- 技术栈: {tech_stack[:180].strip()}")
    if build_files:
        parts.append(f"- 构建入口: {', '.join(build_files)}")
    if source_roots:
        parts.append(f"- 源码目录: {', '.join(source_roots)}")
    if test_roots:
        parts.append(f"- 测试目录: {', '.join(test_roots)}")
    if impl_refs:
        parts.append(f"- 参考实现: {', '.join(impl_refs)}")

    if not parts:
        return _clip_stage_context(
            text,
            limit=_EXECUTION_REPO_HINT_LIMIT,
            marker="...(执行阶段仓库信息已截断)",
        )

    return _clip_stage_context(
        "\n".join(parts),
        limit=_EXECUTION_REPO_HINT_LIMIT,
        marker="...(执行阶段仓库信息已截断)",
    )


def _clip_execution_prior_outputs(prior: List[Dict[str, str]]) -> List[Dict[str, str]]:
    clipped: List[Dict[str, str]] = []
    for item in prior:
        stage = str(item.get("stage") or "").strip()
        output = str(item.get("output") or "")
        limit = _EXECUTION_PRIOR_LIMITS.get(stage.lower(), 720)
        clipped_output = _clip_stage_context(
            output,
            limit=limit,
            marker=_EXECUTION_PRIOR_MARKER,
        ) or ""
        clipped.append({"stage": stage, "output": clipped_output})
    return clipped


def build_user_prompt(ctx: StageContext) -> str:
    """Build the user prompt text for an AgentRunner chat call.

    System prompt is already injected when the AgentRunner is created,
    so this only returns the user-facing message text.
    """
    stage_instruction = STAGE_INSTRUCTIONS.get(ctx.stage_name, "请完成当前阶段的工作。")

    parts: List[str] = []
    parts.append(f"## 任务\n**{ctx.task_title}**")
    if ctx.task_description:
        parts.append(f"\n{ctx.task_description}")

    repo_context = ctx.repo_context
    project_memory = ctx.project_memory
    if _is_execution_stage(ctx.stage_name):
        if ctx.preflight_summary:
            repo_context = None
        else:
            repo_context = _build_execution_repo_hint(repo_context)
        project_memory = _clip_stage_context(
            project_memory,
            limit=_EXECUTION_MEMORY_LIMIT,
            marker="...(执行阶段记忆已截断)",
        )

    # Inject repo context (tech stack + directory structure)
    if repo_context:
        parts.append(f"\n## 项目代码库信息\n{repo_context}")

    # Inject project memory from historical tasks
    if project_memory:
        parts.append(f"\n## 项目上下文（来自历史任务）\n{project_memory}")

    if ctx.preflight_summary:
        parts.append(f"\n## 阶段预扫摘要\n{ctx.preflight_summary}")

    # Use compressed outputs (sliding-window) when available, otherwise raw
    prior = ctx.compressed_outputs if ctx.compressed_outputs is not None else ctx.prior_outputs
    if prior and _is_execution_stage(ctx.stage_name):
        prior = _clip_execution_prior_outputs(prior)
    if prior:
        parts.append("\n## 前序阶段产出")
        for po in prior:
            parts.append(f"\n### {po['stage']} 阶段输出\n{po['output']}")

    # Inject retry context if this is a resumed/retried execution
    if ctx.retry_context:
        error = ctx.retry_context.get("error", "")
        prior_output = ctx.retry_context.get("prior_output", "")
        parts.append("\n## ⚠ 重试上下文（上次执行失败）")
        if error:
            parts.append(f"**失败原因:** {error}")
        # Structured reflection fields (from generate_structured_reflection)
        lesson = ctx.retry_context.get("lesson", "")
        if lesson:
            parts.append(f"**教训:** {lesson}")
        suggestion = ctx.retry_context.get("suggestion", "")
        if suggestion:
            parts.append(f"**建议:** {suggestion}")
        recovery_hint = ctx.retry_context.get("recovery_hint", "")
        if recovery_hint:
            parts.append(f"**恢复指引:** {recovery_hint}")
        if prior_output:
            # Truncate to avoid bloating context
            truncated = prior_output[:2000]
            if len(prior_output) > 2000:
                truncated += "\n...(已截断)"
            parts.append(f"**上次部分输出:**\n{truncated}")
        parts.append("请分析失败原因，避免重复同样的错误，重新完成任务。")

    # Inject gate rejection feedback if this is a retry after gate rejection
    if ctx.gate_rejection_context:
        comment = ctx.gate_rejection_context.get("comment", "")
        retry_info = ctx.gate_rejection_context.get("retry", "")
        parts.append("\n## ⚠ Gate审批被拒绝 — 请根据反馈修改")
        if retry_info:
            parts.append(f"**重试次数:** {retry_info}")
        if comment:
            parts.append(f"**审批者反馈:** {comment}")
        parts.append("请仔细阅读审批反馈，针对性地修改产出，避免重复同样的问题。")

    parts.append(f"\n## 当前阶段: {ctx.stage_name}\n{stage_instruction}")

    guardrail = STAGE_GUARDRAILS.get(ctx.stage_name)
    if guardrail:
        parts.append(f"\n## 阶段边界\n{guardrail}")

    # Append custom instruction from template stage definition (Phase 1.4)
    if ctx.custom_instruction:
        parts.append(f"\n## 附加指令\n{ctx.custom_instruction}")

    return "\n".join(parts)
