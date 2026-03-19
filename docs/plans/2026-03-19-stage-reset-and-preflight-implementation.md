# Stage Reset And Preflight Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Reduce `coding` and `test` token usage by combining deterministic preflight scan summaries with executor-driven rolling conversation resets.

**Architecture:** Add small role-specific preflight summaries ahead of `coding` and `test`, then teach the executor to restart long-running stage conversations from compact checkpoints instead of carrying full multi-turn history forever. Keep the existing stage model and runtime entrypoints intact.

**Tech Stack:** Python, FastAPI worker runtime, SQLAlchemy task pipeline, SkillKit AgentRunner, pytest

---

### Task 1: Add Preflight Summary Builders

**Files:**
- Modify: `/Users/jowang/Documents/github/silicon_agent/platform/app/worker/engine.py`
- Modify: `/Users/jowang/Documents/github/silicon_agent/platform/app/worker/executor.py`
- Test: `/Users/jowang/Documents/github/silicon_agent/platform/tests/test_executor_stage_logs.py`

**Step 1: Write the failing test**

Add tests for a helper that produces compact preflight text for `coding` and `test` from workspace facts.

**Step 2: Run test to verify it fails**

Run:
```bash
cd platform
. .venv/bin/activate
pytest tests/test_executor_stage_logs.py -q
```

Expected: FAIL because the new preflight helper does not exist yet.

**Step 3: Write minimal implementation**

Add helpers that:
- gather lightweight repo facts for `coding` / `test`
- cap output size aggressively
- degrade gracefully when data is missing

Keep the first version simple and deterministic.

**Step 4: Run test to verify it passes**

Run:
```bash
cd platform
. .venv/bin/activate
pytest tests/test_executor_stage_logs.py -q
```

Expected: PASS for the new helper coverage.

**Step 5: Commit**

```bash
git add platform/app/worker/engine.py platform/app/worker/executor.py platform/tests/test_executor_stage_logs.py
git commit -m "feat(worker): add coding and test preflight summaries"
```

### Task 2: Inject Preflight Into Stage Execution

**Files:**
- Modify: `/Users/jowang/Documents/github/silicon_agent/platform/app/worker/engine.py`
- Modify: `/Users/jowang/Documents/github/silicon_agent/platform/app/worker/prompts.py`
- Test: `/Users/jowang/Documents/github/silicon_agent/platform/tests/test_prompts.py`

**Step 1: Write the failing test**

Add tests showing that `coding` / `test` prompts include the preflight summary block when available.

**Step 2: Run test to verify it fails**

Run:
```bash
cd platform
. .venv/bin/activate
pytest tests/test_prompts.py -q
```

Expected: FAIL because prompts do not yet include the new block.

**Step 3: Write minimal implementation**

Extend stage context and prompt assembly so the preflight summary is included only for the roles that need it, and is clearly labeled.

**Step 4: Run test to verify it passes**

Run:
```bash
cd platform
. .venv/bin/activate
pytest tests/test_prompts.py -q
```

Expected: PASS.

**Step 5: Commit**

```bash
git add platform/app/worker/engine.py platform/app/worker/prompts.py platform/tests/test_prompts.py
git commit -m "feat(worker): inject role preflight summaries"
```

### Task 3: Add Rolling Reset Checkpoint Builder

**Files:**
- Modify: `/Users/jowang/Documents/github/silicon_agent/platform/app/worker/executor.py`
- Test: `/Users/jowang/Documents/github/silicon_agent/platform/tests/test_executor_stage_logs.py`

**Step 1: Write the failing test**

Add tests for a helper that turns current stage state into a compact restart checkpoint.

Cover:
- task objective
- current stage goal
- recent tool digest
- next required action

**Step 2: Run test to verify it fails**

Run:
```bash
cd platform
. .venv/bin/activate
pytest tests/test_executor_stage_logs.py -q
```

Expected: FAIL because checkpoint logic does not exist yet.

**Step 3: Write minimal implementation**

Build a compact textual checkpoint helper in the executor and keep it size-capped.

**Step 4: Run test to verify it passes**

Run:
```bash
cd platform
. .venv/bin/activate
pytest tests/test_executor_stage_logs.py -q
```

Expected: PASS.

**Step 5: Commit**

```bash
git add platform/app/worker/executor.py platform/tests/test_executor_stage_logs.py
git commit -m "feat(worker): build compact stage restart checkpoints"
```

### Task 4: Restart Long-Running Stage Conversations From Checkpoints

**Files:**
- Modify: `/Users/jowang/Documents/github/silicon_agent/platform/app/worker/executor.py`
- Test: `/Users/jowang/Documents/github/silicon_agent/platform/tests/test_executor_stage_logs.py`

**Step 1: Write the failing test**

Add tests showing that when churn thresholds are exceeded:
- executor restarts the stage from a checkpoint
- the follow-up chat uses `reset=True`
- repeated raw-history continuation is reduced

**Step 2: Run test to verify it fails**

Run:
```bash
cd platform
. .venv/bin/activate
pytest tests/test_executor_stage_logs.py -q
```

Expected: FAIL because executor still only continues in-place.

**Step 3: Write minimal implementation**

Add restart logic for `coding` and `test` only:
- detect churn threshold
- build checkpoint
- restart with a fresh chat
- preserve existing lifecycle logging

**Step 4: Run test to verify it passes**

Run:
```bash
cd platform
. .venv/bin/activate
pytest tests/test_executor_stage_logs.py -q
```

Expected: PASS.

**Step 5: Commit**

```bash
git add platform/app/worker/executor.py platform/tests/test_executor_stage_logs.py
git commit -m "fix(worker): reset stage chats from compact checkpoints"
```

### Task 5: End-to-End Regression Verification

**Files:**
- Modify: `/Users/jowang/Documents/github/silicon_agent/platform/tests/test_executor_stage_logs.py`
- Modify: `/Users/jowang/Documents/github/silicon_agent/platform/tests/test_prompts.py`

**Step 1: Add missing regression coverage**

Ensure tests cover:
- fallback behavior when preflight is unavailable
- non-target roles unaffected
- prompt size stays bounded
- forced convergence and rolling reset do not fight each other

**Step 2: Run targeted regression suite**

Run:
```bash
cd platform
. .venv/bin/activate
pytest tests/test_executor_stage_logs.py tests/test_prompts.py -q
```

Expected: PASS.

**Step 3: Commit**

```bash
git add platform/tests/test_executor_stage_logs.py platform/tests/test_prompts.py
git commit -m "test(worker): cover stage reset and preflight regressions"
```

### Task 6: Live Validation On VM

**Files:**
- No local code changes required unless fixes are needed

**Step 1: Deploy latest branch to VM**

Pull latest code and restart backend in the current host-execution mode.

**Step 2: Clone known comparison task**

Use:
```bash
POST /api/v1/tasks/339f8bd3-c5f2-4da5-8267-15a6ec3aaaa3/clone
```

**Step 3: Compare live metrics**

Capture:
- `llm_turn_sent`
- `tool_call_executed`
- `Max turns reached`
- total tokens

for `code` and `test`.

**Step 4: Record comparison**

Compare against the recent baselines already observed on VM and summarize whether:
- exploration rounds dropped
- token usage dropped
- repeated truncation dropped

**Step 5: Commit only if code changed during validation**

```bash
git add <files>
git commit -m "fix(worker): adjust stage reset thresholds"
```

## Verification

Primary local verification:

```bash
cd /Users/jowang/Documents/github/silicon_agent/platform
. .venv/bin/activate
pytest tests/test_executor_stage_logs.py tests/test_prompts.py -q
```

Secondary live verification:

- deploy current branch to VM
- clone the known `helloworld` task
- compare `code/test` stage metrics with earlier runs

## Risks

- Restart checkpoints may omit important context and hurt correctness.
- Preflight summaries may become too verbose and recreate the same token problem in a different form.
- Rolling reset may interact awkwardly with current continuation and forced-convergence behavior.

## Mitigations

- Keep checkpoint format deliberately compact and task-focused.
- Hard-cap preflight and checkpoint text.
- Limit the first version to `coding` and `test`.
- Add targeted tests around restart behavior.

## Rollback

Rollback would revert changes in:

- `/Users/jowang/Documents/github/silicon_agent/platform/app/worker/engine.py`
- `/Users/jowang/Documents/github/silicon_agent/platform/app/worker/executor.py`
- `/Users/jowang/Documents/github/silicon_agent/platform/app/worker/prompts.py`
- `/Users/jowang/Documents/github/silicon_agent/platform/tests/test_executor_stage_logs.py`
- `/Users/jowang/Documents/github/silicon_agent/platform/tests/test_prompts.py`
