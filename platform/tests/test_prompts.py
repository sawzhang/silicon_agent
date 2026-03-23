"""Tests for app/worker/prompts.py — build_user_prompt coverage."""
from __future__ import annotations

import pytest

from app.worker.prompts import (
    STAGE_INSTRUCTIONS,
    SYSTEM_PROMPTS,
    StageContext,
    build_user_prompt,
)


def _minimal_ctx(**overrides) -> StageContext:
    """Return a StageContext with minimal required fields, optionally overridden."""
    defaults = dict(
        task_title="Test Task",
        task_description=None,
        stage_name="code",
        agent_role="coding",
        prior_outputs=[],
    )
    defaults.update(overrides)
    return StageContext(**defaults)


# ---------------------------------------------------------------------------
# Minimal context — title only
# ---------------------------------------------------------------------------

def test_minimal_title_only():
    ctx = _minimal_ctx()
    result = build_user_prompt(ctx)
    assert "Test Task" in result
    assert "## 任务" in result
    # Stage instruction for "code" should be present
    assert STAGE_INSTRUCTIONS["code"] in result


def test_code_guardrail_emphasizes_convergence():
    ctx = _minimal_ctx(stage_name="code")
    result = build_user_prompt(ctx)
    assert "不要为了理解整个仓库而广泛探索" in result
    assert "最小必要验证" in result
    assert "最多再检查 3 个关键文件" in result


def test_test_guardrail_emphasizes_minimal_validation():
    ctx = _minimal_ctx(stage_name="test", agent_role="test")
    result = build_user_prompt(ctx)
    assert "最小、最相关、最快的验证路径" in result
    assert "满足验收标准" in result
    assert "执行 2 条验证命令" in result
    assert "不要只根据代码阅读就判定测试通过" in result


def test_dispatch_issue_prompt_contract():
    ctx = _minimal_ctx(
        stage_name="dispatch_issue",
        agent_role="dispatch issue",
        task_description="Issue URL: https://scm.starbucks.com/china/starbucks-asg-api/issues/13",
    )
    result = build_user_prompt(ctx)
    assert "GitHub Issue" in SYSTEM_PROMPTS["dispatch issue"]
    assert "`github_issue_dispatch` skill" in SYSTEM_PROMPTS["dispatch issue"]
    assert "github_issue_dispatch" in STAGE_INSTRUCTIONS["dispatch_issue"]
    assert "不得直接修改任何代码" in STAGE_INSTRUCTIONS["dispatch_issue"]
    assert STAGE_INSTRUCTIONS["dispatch_issue"] in result


def test_dispatch_issue_prompt_embeds_dispatch_skill_body():
    ctx = _minimal_ctx(
        stage_name="dispatch_issue",
        agent_role="dispatch issue",
        task_description="Issue URL: https://scm.starbucks.com/china/starbucks-asg-api/issues/13",
    )
    result = build_user_prompt(ctx)
    assert "## 分发技能" in result
    assert "# GitHub Issue Dispatch Skill" in result
    assert "JSON Schema" in result
    assert "selected_agent_role" in result


def test_issue_distribution_has_single_canonical_prompt_name():
    assert "dispatch issue" in SYSTEM_PROMPTS
    assert "dispatch issue agent" not in SYSTEM_PROMPTS
    assert "dispatch agent" not in SYSTEM_PROMPTS


def test_des_encrypt_prompt_contract():
    ctx = _minimal_ctx(
        stage_name="des encrypt",
        agent_role="des encrypt",
        task_description="Issue #13 要求对 phone 字段进行安全加密",
        prior_outputs=[
            {
                "stage": "dispatch_issue",
                "output": '{"selected_agent_role":"des encrypt","issue_number":13}',
            }
        ],
    )
    result = build_user_prompt(ctx)
    assert "安全加密" in SYSTEM_PROMPTS["des encrypt"]
    assert "des_encrypt" in SYSTEM_PROMPTS["des encrypt"]
    assert "github_issue_feedback" in SYSTEM_PROMPTS["des encrypt"]
    assert STAGE_INSTRUCTIONS["des encrypt"] in result
    assert "dispatch_issue" in result


def test_des_encrypt_prompt_embeds_role_skill_bodies():
    ctx = _minimal_ctx(
        stage_name="des encrypt",
        agent_role="des encrypt",
        task_description="Issue #13 要求对 phone 字段进行安全加密",
    )
    result = build_user_prompt(ctx)
    assert "## 安全加密技能" in result
    assert "# DES 安全加密接入 Skill" in result
    assert "## GitHub 回帖技能" in result
    assert "# GitHub Issue Feedback Skill" in result


def test_issue_stage_instructions_follow_existing_numbered_style():
    assert "github_issue_dispatch" in STAGE_INSTRUCTIONS["dispatch_issue"]
    assert "JSON Schema" in STAGE_INSTRUCTIONS["dispatch_issue"]
    assert "des_encrypt" in STAGE_INSTRUCTIONS["des encrypt"]
    assert "github_issue_feedback" in STAGE_INSTRUCTIONS["des encrypt"]
    assert "1. **Coding**" in STAGE_INSTRUCTIONS["des encrypt"]
    assert "2. **回帖**" in STAGE_INSTRUCTIONS["des encrypt"]


def test_des_encrypt_prompt_forbids_nested_clone():
    ctx = _minimal_ctx(
        stage_name="des encrypt",
        agent_role="des encrypt",
        preflight_summary="- 当前工作区: 目标仓库已在当前 workspace 根目录检出；直接在这里读写、commit、push。",
    )
    result = build_user_prompt(ctx)
    assert "git clone" in result
    assert "当前 workspace 根目录" in result


def test_des_encrypt_prompt_enforces_minimal_issue_scope():
    ctx = _minimal_ctx(
        stage_name="des encrypt",
        agent_role="des encrypt",
        task_description="Issue #13: 仅对 phone 字段进行安全加密",
    )
    result = build_user_prompt(ctx)
    # Minimal scope enforcement comes from guardrail and des_encrypt skill
    assert "单一字段" in result or "最小改造模式" in result
    assert "logback" in result or "环境模板" in result


# ---------------------------------------------------------------------------
# With description
# ---------------------------------------------------------------------------

def test_with_description():
    ctx = _minimal_ctx(task_description="A detailed description of the task.")
    result = build_user_prompt(ctx)
    assert "A detailed description of the task." in result


def test_without_description_no_empty_section():
    ctx = _minimal_ctx(task_description=None)
    result = build_user_prompt(ctx)
    # No description appended when it is None
    assert result.count("\n\n") < 5  # sanity: no runaway blank sections


# ---------------------------------------------------------------------------
# With repo_context
# ---------------------------------------------------------------------------

def test_with_repo_context():
    ctx = _minimal_ctx(repo_context="Python 3.11 / FastAPI / SQLite\n./app/\n  main.py")
    result = build_user_prompt(ctx)
    assert "## 项目代码库信息" in result
    assert "Python 3.11 / FastAPI" in result


def test_code_stage_clips_large_repo_context():
    repo_context = (
        "### 技术栈\nJava 17, Spring Boot, Gradle\n\n"
        "### 目录结构\n"
        "build.gradle\n"
        "src/main/java/demo/controller/HelloController.java\n"
        "src/main/java/demo/service/HelloService.java\n"
        "src/test/java/demo/controller/HelloControllerTest.java\n"
        "docs/design.md\n"
    )
    ctx = _minimal_ctx(stage_name="code", agent_role="coding", repo_context=repo_context)
    result = build_user_prompt(ctx)
    assert "## 项目代码库信息" in result
    assert "- 技术栈: Java 17, Spring Boot, Gradle" in result
    assert "- 构建入口: build.gradle" in result
    assert "- 源码目录:" in result
    assert "- 测试目录:" in result
    assert "- 参考实现:" in result
    assert "### 目录结构" not in result


def test_spec_stage_keeps_full_repo_context():
    repo_context = "STACK\n" + ("src/main/java/demo/File.java\n" * 40)
    ctx = _minimal_ctx(stage_name="spec", agent_role="spec", repo_context=repo_context)
    result = build_user_prompt(ctx)
    assert "...(执行阶段上下文已截断)" not in result
    assert repo_context in result


def test_code_stage_omits_repo_context_when_preflight_present():
    ctx = _minimal_ctx(
        stage_name="code",
        agent_role="coding",
        repo_context="STACK\nsrc/main/java/demo/File.java",
        preflight_summary="- 构建文件: build.gradle",
    )
    result = build_user_prompt(ctx)
    assert "## 项目代码库信息" not in result
    assert "## 阶段预扫摘要" in result


def test_without_repo_context():
    ctx = _minimal_ctx(repo_context=None)
    result = build_user_prompt(ctx)
    assert "## 项目代码库信息" not in result


# ---------------------------------------------------------------------------
# With project_memory
# ---------------------------------------------------------------------------

def test_with_project_memory():
    ctx = _minimal_ctx(project_memory="Previous task: added auth module.")
    result = build_user_prompt(ctx)
    assert "## 项目上下文（来自历史任务）" in result
    assert "Previous task: added auth module." in result


def test_test_stage_clips_large_project_memory():
    project_memory = "Memory line\n" * 300
    ctx = _minimal_ctx(stage_name="test", agent_role="test", project_memory=project_memory)
    result = build_user_prompt(ctx)
    assert "## 项目上下文（来自历史任务）" in result
    assert "...(执行阶段记忆已截断)" in result
    assert len(result) < len(project_memory) + 500


def test_without_project_memory():
    ctx = _minimal_ctx(project_memory=None)
    result = build_user_prompt(ctx)
    assert "## 项目上下文（来自历史任务）" not in result


def test_with_preflight_summary():
    ctx = _minimal_ctx(preflight_summary="- 构建文件: build.gradle\n- 实现参考: src/main/java/demo/HelloController.java")
    result = build_user_prompt(ctx)
    assert "## 阶段预扫摘要" in result
    assert "HelloController.java" in result


def test_without_preflight_summary():
    ctx = _minimal_ctx(preflight_summary=None)
    result = build_user_prompt(ctx)
    assert "## 阶段预扫摘要" not in result


# ---------------------------------------------------------------------------
# With prior_outputs (raw)
# ---------------------------------------------------------------------------

def test_with_prior_outputs_raw():
    prior = [
        {"stage": "parse", "output": "Parsed requirements: ..."},
        {"stage": "spec", "output": "Spec document: ..."},
    ]
    ctx = _minimal_ctx(prior_outputs=prior)
    result = build_user_prompt(ctx)
    assert "## 前序阶段产出" in result
    assert "### parse 阶段输出" in result
    assert "Parsed requirements:" in result
    assert "### spec 阶段输出" in result
    assert "Spec document:" in result


def test_execution_stage_clips_prior_outputs_aggressively():
    long_parse = "需求分析\n" + ("parse-line\n" * 200)
    long_spec = "技术方案\n" + ("spec-line\n" * 200)
    ctx = _minimal_ctx(
        stage_name="code",
        agent_role="coding",
        prior_outputs=[
            {"stage": "parse", "output": long_parse},
            {"stage": "spec", "output": long_spec},
        ],
    )
    result = build_user_prompt(ctx)
    assert "## 前序阶段产出" in result
    assert "...(前序阶段产出已截断)" in result
    assert "parse-line\nparse-line\nparse-line" in result
    assert result.count("parse-line") < 80
    assert result.count("spec-line") < 100


def test_with_empty_prior_outputs():
    ctx = _minimal_ctx(prior_outputs=[])
    result = build_user_prompt(ctx)
    assert "## 前序阶段产出" not in result


# ---------------------------------------------------------------------------
# With compressed_outputs (takes precedence over prior_outputs)
# ---------------------------------------------------------------------------

def test_compressed_outputs_take_precedence():
    raw = [{"stage": "parse", "output": "RAW OUTPUT"}]
    compressed = [{"stage": "parse", "output": "COMPRESSED OUTPUT"}]
    ctx = _minimal_ctx(prior_outputs=raw, compressed_outputs=compressed)
    result = build_user_prompt(ctx)
    assert "COMPRESSED OUTPUT" in result
    assert "RAW OUTPUT" not in result


def test_compressed_outputs_empty_list_hides_section():
    """An explicitly empty compressed_outputs list means no prior section."""
    raw = [{"stage": "parse", "output": "RAW OUTPUT"}]
    ctx = _minimal_ctx(prior_outputs=raw, compressed_outputs=[])
    result = build_user_prompt(ctx)
    # compressed_outputs=[] is not None, so it takes precedence — section absent
    assert "## 前序阶段产出" not in result
    assert "RAW OUTPUT" not in result


def test_compressed_outputs_none_falls_back_to_prior():
    raw = [{"stage": "parse", "output": "RAW OUTPUT"}]
    ctx = _minimal_ctx(prior_outputs=raw, compressed_outputs=None)
    result = build_user_prompt(ctx)
    assert "RAW OUTPUT" in result


# ---------------------------------------------------------------------------
# With retry_context — basic fields
# ---------------------------------------------------------------------------

def test_retry_context_with_error():
    ctx = _minimal_ctx(retry_context={"error": "TimeoutError: stage exceeded 300s"})
    result = build_user_prompt(ctx)
    assert "## ⚠ 重试上下文（上次执行失败）" in result
    assert "**失败原因:** TimeoutError: stage exceeded 300s" in result
    assert "请分析失败原因" in result


def test_retry_context_with_lesson_and_suggestion():
    ctx = _minimal_ctx(
        retry_context={
            "error": "SyntaxError",
            "lesson": "Do not mix tabs and spaces",
            "suggestion": "Use ruff to lint before returning",
        }
    )
    result = build_user_prompt(ctx)
    assert "**教训:** Do not mix tabs and spaces" in result
    assert "**建议:** Use ruff to lint before returning" in result


def test_retry_context_with_prior_output_short():
    """Short prior_output (< 2000 chars) should appear verbatim."""
    ctx = _minimal_ctx(retry_context={"prior_output": "Some previous output text."})
    result = build_user_prompt(ctx)
    assert "**上次部分输出:**" in result
    assert "Some previous output text." in result
    assert "已截断" not in result


def test_retry_context_with_prior_output_long():
    """prior_output > 2000 chars must be truncated with ellipsis marker."""
    long_text = "x" * 2500
    ctx = _minimal_ctx(retry_context={"prior_output": long_text})
    result = build_user_prompt(ctx)
    assert "**上次部分输出:**" in result
    assert "...(已截断)" in result
    # The truncated portion should be exactly 2000 "x" chars
    assert "x" * 2000 in result
    assert "x" * 2001 not in result


def test_retry_context_empty_dict():
    """An empty retry_context dict is falsy — no retry section is added."""
    ctx = _minimal_ctx(retry_context={})
    result = build_user_prompt(ctx)
    # Empty dict is falsy in Python, so the retry block is skipped entirely
    assert "## ⚠ 重试上下文（上次执行失败）" not in result


def test_no_retry_context():
    ctx = _minimal_ctx(retry_context=None)
    result = build_user_prompt(ctx)
    assert "## ⚠ 重试上下文" not in result


# ---------------------------------------------------------------------------
# With gate_rejection_context
# ---------------------------------------------------------------------------

def test_gate_rejection_context_with_comment_and_retry():
    ctx = _minimal_ctx(
        gate_rejection_context={"comment": "Needs more tests.", "retry": "2/3"}
    )
    result = build_user_prompt(ctx)
    assert "## ⚠ Gate审批被拒绝 — 请根据反馈修改" in result
    assert "**审批者反馈:** Needs more tests." in result
    assert "**重试次数:** 2/3" in result
    assert "请仔细阅读审批反馈" in result


def test_gate_rejection_context_only_comment():
    ctx = _minimal_ctx(gate_rejection_context={"comment": "Please revise the API design."})
    result = build_user_prompt(ctx)
    assert "**审批者反馈:** Please revise the API design." in result
    # No retry_info → retry line absent
    assert "**重试次数:**" not in result


def test_gate_rejection_context_only_retry():
    ctx = _minimal_ctx(gate_rejection_context={"retry": "1/3"})
    result = build_user_prompt(ctx)
    assert "**重试次数:** 1/3" in result
    assert "**审批者反馈:**" not in result


def test_gate_rejection_context_empty_dict():
    """An empty gate_rejection_context dict is falsy — no gate section is added."""
    ctx = _minimal_ctx(gate_rejection_context={})
    result = build_user_prompt(ctx)
    # Empty dict is falsy in Python, so the gate rejection block is skipped
    assert "## ⚠ Gate审批被拒绝 — 请根据反馈修改" not in result


def test_no_gate_rejection_context():
    ctx = _minimal_ctx(gate_rejection_context=None)
    result = build_user_prompt(ctx)
    assert "## ⚠ Gate审批被拒绝" not in result


# ---------------------------------------------------------------------------
# With custom_instruction
# ---------------------------------------------------------------------------

def test_with_custom_instruction():
    ctx = _minimal_ctx(custom_instruction="Output must be valid JSON only.")
    result = build_user_prompt(ctx)
    assert "## 附加指令" in result
    assert "Output must be valid JSON only." in result


def test_without_custom_instruction():
    ctx = _minimal_ctx(custom_instruction=None)
    result = build_user_prompt(ctx)
    assert "## 附加指令" not in result


def test_code_stage_includes_boundary_against_signoff():
    ctx = _minimal_ctx(stage_name="code", agent_role="coding")
    result = build_user_prompt(ctx)
    assert "只完成当前阶段" in result
    assert "不要提前生成最终签收" in result
    assert "不要调用 signoff" in result


def test_test_stage_includes_stop_condition_after_relevant_tests_pass():
    ctx = _minimal_ctx(stage_name="test", agent_role="test")
    result = build_user_prompt(ctx)
    assert "相关测试已经通过" in result
    assert "立即停止" in result
    assert "不要继续扩展额外类型的测试" in result
    assert "E2E" in result


def test_signoff_stage_prefers_latest_verified_state_over_stale_prior_output():
    ctx = _minimal_ctx(stage_name="signoff", agent_role="orchestrator")
    result = build_user_prompt(ctx)
    assert "中间态、自修复前描述或已过时结论" in result
    assert "最新事实为准" in result
    assert "已解决" in result
    assert "不要继续当作遗留问题" in result
    assert "优先复用 test 阶段已经完成的最终验证结果" in result
    assert "不要重复安装依赖" in result


# ---------------------------------------------------------------------------
# Stage name → STAGE_INSTRUCTIONS lookup
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("stage_name", list(STAGE_INSTRUCTIONS.keys()))
def test_known_stage_uses_stage_instructions(stage_name):
    ctx = _minimal_ctx(stage_name=stage_name)
    result = build_user_prompt(ctx)
    assert STAGE_INSTRUCTIONS[stage_name] in result


def test_unknown_stage_falls_back_to_default():
    ctx = _minimal_ctx(stage_name="completely_unknown_stage")
    result = build_user_prompt(ctx)
    assert "请完成当前阶段的工作。" in result
    assert "## 当前阶段: completely_unknown_stage" in result


# ---------------------------------------------------------------------------
# All sections combined
# ---------------------------------------------------------------------------

def test_all_sections_combined():
    ctx = StageContext(
        task_title="Big Combined Task",
        task_description="Full description here.",
        stage_name="review",
        agent_role="review",
        prior_outputs=[{"stage": "code", "output": "Code output text"}],
        compressed_outputs=None,
        project_memory="Memory from past tasks.",
        repo_context="FastAPI project.",
        retry_context={
            "error": "RuntimeError",
            "prior_output": "partial output",
            "lesson": "check imports",
            "suggestion": "fix the import path",
        },
        gate_rejection_context={"comment": "Needs refactor.", "retry": "3/3"},
        custom_instruction="Focus on security issues.",
    )
    result = build_user_prompt(ctx)

    # Task section
    assert "## 任务" in result
    assert "**Big Combined Task**" in result
    assert "Full description here." in result
    # Repo context
    assert "## 项目代码库信息" in result
    assert "FastAPI project." in result
    # Project memory
    assert "## 项目上下文（来自历史任务）" in result
    assert "Memory from past tasks." in result
    # Prior outputs
    assert "## 前序阶段产出" in result
    assert "Code output text" in result
    # Retry context
    assert "## ⚠ 重试上下文（上次执行失败）" in result
    assert "**失败原因:** RuntimeError" in result
    assert "**教训:** check imports" in result
    assert "**建议:** fix the import path" in result
    assert "**上次部分输出:**" in result
    # Gate rejection context
    assert "## ⚠ Gate审批被拒绝 — 请根据反馈修改" in result
    assert "**审批者反馈:** Needs refactor." in result
    # Stage instruction
    assert "## 当前阶段: review" in result
    assert STAGE_INSTRUCTIONS["review"] in result
    # Custom instruction
    assert "## 附加指令" in result
    assert "Focus on security issues." in result
