# Coding And Testing Convergence Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Tighten `coding` and `test` stage convergence by strengthening stage guardrails and using stage-specific continuation prompts.

**Architecture:** Keep the execution chain unchanged and localize the work to prompt assembly and continuation handling. `prompts.py` will define stronger convergence instructions for `coding` and `test`, and `executor.py` will choose stage-aware continuation prompts when the runner hits the truncation sentinel.

**Tech Stack:** Python, pytest, async worker executor, stage prompt generation

---

### Task 1: Strengthen stage guardrails in prompt generation

**Files:**
- Modify: `/Users/jowang/Documents/github/silicon_agent/platform/app/worker/prompts.py`
- Test: `/Users/jowang/Documents/github/silicon_agent/platform/tests/test_prompts.py`

**Step 1: Write the failing tests**

Add prompt assertions that prove:

- the `code` guardrail tells the agent to avoid broad repo exploration and move toward concrete edits plus minimal validation;
- the `test` guardrail tells the agent to use the smallest relevant verification path and stop once evidence is sufficient.

Example assertions:

```python
def test_code_prompt_emphasizes_convergence():
    ctx = _minimal_ctx(stage_name="code")
    result = build_user_prompt(ctx)
    assert "不要为了理解整个仓库而广泛探索" in result
    assert "最小必要验证" in result


def test_test_prompt_emphasizes_minimal_validation():
    ctx = _minimal_ctx(stage_name="test")
    result = build_user_prompt(ctx)
    assert "最小、最相关、最快的验证路径" in result
    assert "满足验收标准" in result
```

**Step 2: Run the tests to verify they fail**

Run:

```bash
cd /Users/jowang/Documents/github/silicon_agent/platform && . .venv/bin/activate && pytest tests/test_prompts.py -k "convergence or minimal_validation" -q
```

Expected: FAIL because the current guardrails do not contain the new phrases.

**Step 3: Write the minimal implementation**

Update `STAGE_GUARDRAILS["code"]` and `STAGE_GUARDRAILS["test"]` in [prompts.py](/Users/jowang/Documents/github/silicon_agent/platform/app/worker/prompts.py) so they:

- push `coding` toward immediate implementation and minimal verification;
- push `test` toward targeted validation and fast termination once evidence is enough.

Keep the wording concise and consistent with the existing Chinese prompt style.

**Step 4: Run the tests to verify they pass**

Run:

```bash
cd /Users/jowang/Documents/github/silicon_agent/platform && . .venv/bin/activate && pytest tests/test_prompts.py -k "convergence or minimal_validation" -q
```

Expected: PASS.

**Step 5: Commit**

```bash
git add /Users/jowang/Documents/github/silicon_agent/platform/app/worker/prompts.py /Users/jowang/Documents/github/silicon_agent/platform/tests/test_prompts.py
git commit -m "fix(worker): tighten coding and test stage guardrails"
```

### Task 2: Add stage-specific continuation prompts

**Files:**
- Modify: `/Users/jowang/Documents/github/silicon_agent/platform/app/worker/executor.py`
- Test: `/Users/jowang/Documents/github/silicon_agent/platform/tests/test_executor_stage_logs.py`

**Step 1: Write the failing tests**

Add executor-focused tests that exercise `_handle_continuations()` and prove:

- `coding` continuations use a convergence prompt that stops broad exploration and asks for concrete edits or one blocker;
- `test` continuations use a convergence prompt that asks for the smallest relevant validation result;
- non-`coding` / non-`test` stages still use the generic continuation prompt.

Use a fake runner whose `chat()` captures the continuation prompt and returns a non-truncated response.

Example structure:

```python
@pytest.mark.asyncio
async def test_handle_continuations_uses_coding_specific_prompt():
    runner = FakeRunner(["done"])
    tracker = FakeTracker()
    output, _tokens = await _handle_continuations(
        runner,
        "[Max turns reached. Please continue the conversation.]",
        {"stage_name": "code"},
        tracker,
    )
    assert "不要继续广泛浏览代码库" in runner.prompts[0]
```

If `_handle_continuations()` does not currently know the stage, first design the smallest helper change that allows the caller to pass it in.

**Step 2: Run the tests to verify they fail**

Run:

```bash
cd /Users/jowang/Documents/github/silicon_agent/platform && . .venv/bin/activate && pytest tests/test_executor_stage_logs.py -k "continuation and coding or continuation and test" -q
```

Expected: FAIL because continuation prompts are currently generic.

**Step 3: Write the minimal implementation**

In [executor.py](/Users/jowang/Documents/github/silicon_agent/platform/app/worker/executor.py):

- introduce a small helper that returns the continuation prompt for a given stage name;
- use stage-aware prompts for `code` and `test`;
- keep a generic fallback for other stages;
- thread the current stage name into `_handle_continuations()` with the smallest possible call-site change.

Do not change retry counts, timeout behavior, logging contracts, or runner reset behavior.

**Step 4: Run the tests to verify they pass**

Run:

```bash
cd /Users/jowang/Documents/github/silicon_agent/platform && . .venv/bin/activate && pytest tests/test_executor_stage_logs.py -k "continuation and coding or continuation and test" -q
```

Expected: PASS.

**Step 5: Commit**

```bash
git add /Users/jowang/Documents/github/silicon_agent/platform/app/worker/executor.py /Users/jowang/Documents/github/silicon_agent/platform/tests/test_executor_stage_logs.py
git commit -m "fix(worker): add convergent continuation prompts"
```

### Task 3: Run regression checks for prompt and executor paths

**Files:**
- Test: `/Users/jowang/Documents/github/silicon_agent/platform/tests/test_prompts.py`
- Test: `/Users/jowang/Documents/github/silicon_agent/platform/tests/test_executor_stage_logs.py`

**Step 1: Run the prompt tests**

Run:

```bash
cd /Users/jowang/Documents/github/silicon_agent/platform && . .venv/bin/activate && pytest tests/test_prompts.py -q
```

Expected: PASS.

**Step 2: Run the executor tests**

Run:

```bash
cd /Users/jowang/Documents/github/silicon_agent/platform && . .venv/bin/activate && pytest tests/test_executor_stage_logs.py -q
```

Expected: PASS.

**Step 3: Run a focused combined smoke check**

Run:

```bash
cd /Users/jowang/Documents/github/silicon_agent/platform && . .venv/bin/activate && pytest tests/test_prompts.py tests/test_executor_stage_logs.py -q
```

Expected: PASS with no new failures in the touched prompt and continuation logic.

**Step 4: Commit the verification state**

```bash
git add /Users/jowang/Documents/github/silicon_agent/docs/plans/2026-03-19-coding-testing-convergence-design.md /Users/jowang/Documents/github/silicon_agent/docs/plans/2026-03-19-coding-testing-convergence-implementation.md
git commit -m "docs: plan coding and testing convergence changes"
```

### Task 4: Optional manual validation on the VM

**Files:**
- Modify: none
- Test: none

**Step 1: Restart the VM worker with the intended config**

Run on the VM after code deployment:

```bash
grep -n 'SANDBOX_ENABLED' /home/stb_admin/silicon_agent/platform/.env
```

Expected: whichever value is desired for the validation session.

**Step 2: Re-run a previously slow task shape**

Use a simple API task similar to “create a helloworld interface”.

**Step 3: Inspect logs**

Check that:

- `coding` does not spend most turns on broad repo exploration;
- continuation prompts no longer produce repeated generic continuation loops;
- `test` stops after targeted validation.

**Step 4: Record any follow-up gaps**

If the new prompt strategy is still too weak, capture concrete examples before considering max-turn tuning in a later change.
