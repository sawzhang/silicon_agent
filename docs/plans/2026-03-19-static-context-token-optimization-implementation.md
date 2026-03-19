# Static Context Token Optimization Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Reduce `code` / `test` token cost by shrinking repeated static prompt overhead before attempting provider-specific prompt caching.

**Architecture:** Reuse the existing worker pipeline, but make execution stages cheaper by routing lighter models where appropriate, removing unnecessary tool exposure, slimming injected repo/memory context, and making compact checkpoint restart the default continuation shape. Preserve the existing stage graph and existing preflight work.

**Tech Stack:** Python, FastAPI worker runtime, SkillKit AgentRunner, pytest

---

### Task 1: Tighten Role-Based Model Routing Defaults

**Files:**
- Modify: `/Users/jowang/Documents/github/silicon_agent/platform/app/worker/agents.py`
- Modify: `/Users/jowang/Documents/github/silicon_agent/platform/app/config.py`
- Modify: `/Users/jowang/Documents/github/silicon_agent/platform/.env.example`
- Test: `/Users/jowang/Documents/github/silicon_agent/platform/tests/test_agents_api.py`

**Step 1: Write the failing test**

Add coverage showing that `parse`, `test`, and `signoff` can resolve to lightweight models through `LLM_ROLE_MODEL_MAP`, while `code` can still resolve to the stronger coding model.

**Step 2: Run test to verify it fails**

Run:
```bash
cd /Users/jowang/Documents/github/silicon_agent/platform
. .venv/bin/activate
pytest tests/test_agents_api.py -q
```

Expected: FAIL until the default config and resolution behavior match the new mapping expectation.

**Step 3: Write minimal implementation**

Update config defaults and environment examples so the role-model map favors:

- light model for `parse`
- strong model for `code`
- light model for `test`
- light model for `signoff`

Keep stage-level override precedence unchanged.

**Step 4: Run test to verify it passes**

Run:
```bash
cd /Users/jowang/Documents/github/silicon_agent/platform
. .venv/bin/activate
pytest tests/test_agents_api.py -q
```

Expected: PASS.

**Step 5: Commit**

```bash
git add /Users/jowang/Documents/github/silicon_agent/platform/app/worker/agents.py /Users/jowang/Documents/github/silicon_agent/platform/app/config.py /Users/jowang/Documents/github/silicon_agent/platform/.env.example /Users/jowang/Documents/github/silicon_agent/platform/tests/test_agents_api.py
git commit -m "config(worker): tune role model routing defaults"
```

### Task 2: Make Signoff Text-Only By Default

**Files:**
- Modify: `/Users/jowang/Documents/github/silicon_agent/platform/app/worker/executor.py`
- Modify: `/Users/jowang/Documents/github/silicon_agent/platform/app/worker/engine.py`
- Test: `/Users/jowang/Documents/github/silicon_agent/platform/tests/test_executor_stage_logs.py`
- Test: `/Users/jowang/Documents/github/silicon_agent/platform/tests/test_engine_stage_execution.py`

**Step 1: Write the failing test**

Add tests showing that `signoff` uses the text-only runner path and does not request tool execution by default.

**Step 2: Run test to verify it fails**

Run:
```bash
cd /Users/jowang/Documents/github/silicon_agent/platform
. .venv/bin/activate
pytest tests/test_executor_stage_logs.py tests/test_engine_stage_execution.py -q
```

Expected: FAIL because `signoff` still flows through the general tool-enabled path.

**Step 3: Write minimal implementation**

Route `signoff` through `get_agent_text_only(...)` and keep it based on prior outputs plus prompt guidance, without reopening tool-driven exploration.

**Step 4: Run test to verify it passes**

Run:
```bash
cd /Users/jowang/Documents/github/silicon_agent/platform
. .venv/bin/activate
pytest tests/test_executor_stage_logs.py tests/test_engine_stage_execution.py -q
```

Expected: PASS.

**Step 5: Commit**

```bash
git add /Users/jowang/Documents/github/silicon_agent/platform/app/worker/executor.py /Users/jowang/Documents/github/silicon_agent/platform/app/worker/engine.py /Users/jowang/Documents/github/silicon_agent/platform/tests/test_executor_stage_logs.py /Users/jowang/Documents/github/silicon_agent/platform/tests/test_engine_stage_execution.py
git commit -m "fix(worker): make signoff text only by default"
```

### Task 3: Prune Stage Tool And Skill Exposure

**Files:**
- Modify: `/Users/jowang/Documents/github/silicon_agent/platform/app/worker/agents.py`
- Modify: `/Users/jowang/Documents/github/silicon_agent/platform/app/worker/executor.py`
- Modify: `/Users/jowang/Documents/github/silicon_agent/platform/sandbox/agent_server.py`
- Test: `/Users/jowang/Documents/github/silicon_agent/platform/tests/test_agents.py`
- Test: `/Users/jowang/Documents/github/silicon_agent/platform/tests/test_executor_stage_logs.py`

**Step 1: Write the failing test**

Add tests showing that:

- `code` only gets core implementation tools/skills
- `test` only gets core verification tools/skills
- `signoff` gets no tool-enabled skill exposure

**Step 2: Run test to verify it fails**

Run:
```bash
cd /Users/jowang/Documents/github/silicon_agent/platform
. .venv/bin/activate
pytest tests/test_agents.py tests/test_executor_stage_logs.py -q
```

Expected: FAIL because role defaults still expose too much shared capability.

**Step 3: Write minimal implementation**

Tighten `ROLE_TOOLS` and role skill directory selection so execution stages only carry what they actually need. Preserve sandbox parity for the container path.

**Step 4: Run test to verify it passes**

Run:
```bash
cd /Users/jowang/Documents/github/silicon_agent/platform
. .venv/bin/activate
pytest tests/test_agents.py tests/test_executor_stage_logs.py -q
```

Expected: PASS.

**Step 5: Commit**

```bash
git add /Users/jowang/Documents/github/silicon_agent/platform/app/worker/agents.py /Users/jowang/Documents/github/silicon_agent/platform/app/worker/executor.py /Users/jowang/Documents/github/silicon_agent/platform/sandbox/agent_server.py /Users/jowang/Documents/github/silicon_agent/platform/tests/test_agents.py /Users/jowang/Documents/github/silicon_agent/platform/tests/test_executor_stage_logs.py
git commit -m "refactor(worker): prune stage tool and skill exposure"
```

### Task 4: Slim Repo Context And Project Memory For Execution Stages

**Files:**
- Modify: `/Users/jowang/Documents/github/silicon_agent/platform/app/worker/engine.py`
- Modify: `/Users/jowang/Documents/github/silicon_agent/platform/app/worker/prompts.py`
- Test: `/Users/jowang/Documents/github/silicon_agent/platform/tests/test_prompts.py`
- Test: `/Users/jowang/Documents/github/silicon_agent/platform/tests/test_worker.py`
- Test: `/Users/jowang/Documents/github/silicon_agent/platform/tests/test_engine_stage_execution.py`

**Step 1: Write the failing test**

Add tests showing that `code` / `test` receive a slim execution-context variant while planning stages can still receive broader repo context.

Cover:

- reduced repo tree content for execution stages
- reduced memory excerpt size for execution stages
- preflight summary still present

**Step 2: Run test to verify it fails**

Run:
```bash
cd /Users/jowang/Documents/github/silicon_agent/platform
. .venv/bin/activate
pytest tests/test_prompts.py tests/test_worker.py tests/test_engine_stage_execution.py -q
```

Expected: FAIL because execution stages still receive the broader context shape.

**Step 3: Write minimal implementation**

Split context construction into broad planning context versus slim execution context. Keep concise build/stack facts, but avoid re-sending large directory trees and long memory blocks to `code` / `test`.

**Step 4: Run test to verify it passes**

Run:
```bash
cd /Users/jowang/Documents/github/silicon_agent/platform
. .venv/bin/activate
pytest tests/test_prompts.py tests/test_worker.py tests/test_engine_stage_execution.py -q
```

Expected: PASS.

**Step 5: Commit**

```bash
git add /Users/jowang/Documents/github/silicon_agent/platform/app/worker/engine.py /Users/jowang/Documents/github/silicon_agent/platform/app/worker/prompts.py /Users/jowang/Documents/github/silicon_agent/platform/tests/test_prompts.py /Users/jowang/Documents/github/silicon_agent/platform/tests/test_worker.py /Users/jowang/Documents/github/silicon_agent/platform/tests/test_engine_stage_execution.py
git commit -m "feat(worker): slim execution stage context payloads"
```

### Task 5: Make Compact Restart The Primary Continuation Shape

**Files:**
- Modify: `/Users/jowang/Documents/github/silicon_agent/platform/app/worker/executor.py`
- Modify: `/Users/jowang/Documents/github/silicon_agent/platform/sandbox/agent_server.py`
- Test: `/Users/jowang/Documents/github/silicon_agent/platform/tests/test_executor_stage_logs.py`
- Test: `/Users/jowang/Documents/github/silicon_agent/platform/tests/test_sandbox_agent_server.py`

**Step 1: Write the failing test**

Add tests showing that after exploration drift or truncation, the stage continuation path prefers compact `reset=True` restart payloads and logs restart metadata consistently.

Cover:

- restart metadata on host path
- restart metadata on sandbox path
- reduced carry-forward payload shape

**Step 2: Run test to verify it fails**

Run:
```bash
cd /Users/jowang/Documents/github/silicon_agent/platform
. .venv/bin/activate
pytest tests/test_executor_stage_logs.py tests/test_sandbox_agent_server.py -q
```

Expected: FAIL because current live behavior still does not consistently surface restart metadata and still leaks too much old context into continuation.

**Step 3: Write minimal implementation**

Promote compact restart to the default continuation path for `code` / `test`, ensure restart logging is explicit, and keep carried state limited to the compact checkpoint.

**Step 4: Run test to verify it passes**

Run:
```bash
cd /Users/jowang/Documents/github/silicon_agent/platform
. .venv/bin/activate
pytest tests/test_executor_stage_logs.py tests/test_sandbox_agent_server.py -q
```

Expected: PASS.

**Step 5: Commit**

```bash
git add /Users/jowang/Documents/github/silicon_agent/platform/app/worker/executor.py /Users/jowang/Documents/github/silicon_agent/platform/sandbox/agent_server.py /Users/jowang/Documents/github/silicon_agent/platform/tests/test_executor_stage_logs.py /Users/jowang/Documents/github/silicon_agent/platform/tests/test_sandbox_agent_server.py
git commit -m "fix(worker): prefer compact restart continuations"
```

### Task 6: Regression And Live Validation

**Files:**
- Modify if needed: `/Users/jowang/Documents/github/silicon_agent/platform/tests/test_agents.py`
- Modify if needed: `/Users/jowang/Documents/github/silicon_agent/platform/tests/test_agents_api.py`
- Modify if needed: `/Users/jowang/Documents/github/silicon_agent/platform/tests/test_prompts.py`
- Modify if needed: `/Users/jowang/Documents/github/silicon_agent/platform/tests/test_executor_stage_logs.py`
- Modify if needed: `/Users/jowang/Documents/github/silicon_agent/platform/tests/test_engine_stage_execution.py`

**Step 1: Run targeted local regression**

Run:
```bash
cd /Users/jowang/Documents/github/silicon_agent/platform
. .venv/bin/activate
pytest tests/test_agents.py tests/test_agents_api.py tests/test_prompts.py tests/test_executor_stage_logs.py tests/test_engine_stage_execution.py tests/test_sandbox_agent_server.py -q
```

Expected: PASS.

**Step 2: Deploy to VM and validate against the known hello-world task family**

Use the VM flow already established for:

- pulling `origin/codex/raise-cb-and-optimize-coding-image`
- restarting backend
- cloning task `339f8bd3-c5f2-4da5-8267-15a6ec3aaaa3`

Capture:

- total task tokens
- `parse` / `code` / `test` stage tokens
- `chat_sent`
- `tool_calls`
- `max_turn_markers`
- `forced_convergence`
- `restart_count`

**Step 3: Commit**

If only test updates were needed:

```bash
git add /Users/jowang/Documents/github/silicon_agent/platform/tests/test_agents.py /Users/jowang/Documents/github/silicon_agent/platform/tests/test_agents_api.py /Users/jowang/Documents/github/silicon_agent/platform/tests/test_prompts.py /Users/jowang/Documents/github/silicon_agent/platform/tests/test_executor_stage_logs.py /Users/jowang/Documents/github/silicon_agent/platform/tests/test_engine_stage_execution.py /Users/jowang/Documents/github/silicon_agent/platform/tests/test_sandbox_agent_server.py
git commit -m "test(worker): cover static context token optimization regressions"
```
