# Exploration Budget Convergence Implementation Plan

## Objective

Implement executor-level exploration-budget convergence for `coding` and `test` stages, with one forced-convergence recovery prompt and targeted regression coverage.

## Scope

- Modify executor behavior only.
- Add or update tests for executor behavior.
- Do not alter stage orchestration, sandbox APIs, or AgentRunner interfaces.

## Planned Changes

### 1. Add lightweight stage exploration state

Update [executor.py](/Users/jowang/Documents/github/silicon_agent/platform/app/worker/executor.py) to maintain per-stage exploration state during execution.

Planned state:

- exploration action counter
- test-command failure flag or summary
- whether forced convergence was already injected

### 2. Define budget heuristics

Add small helper functions in [executor.py](/Users/jowang/Documents/github/silicon_agent/platform/app/worker/executor.py) to classify tool behavior and decide whether the stage has exceeded its budget.

Initial heuristics should stay simple:

- `coding`: repeated read/search/list behavior without implementation progress
- `test`: repeated read/search/list behavior, or failure of validation commands followed by continued drift

### 3. Inject one forced-convergence prompt

Extend the stage execution loop in [executor.py](/Users/jowang/Documents/github/silicon_agent/platform/app/worker/executor.py) so that when the budget is exceeded:

- a stage-specific forced prompt is sent once
- the prompt differs from the existing generic continuation prompt
- subsequent looping does not repeatedly inject the same recovery prompt

### 4. Keep current completion/failure semantics

Preserve existing lifecycle behavior:

- normal successful stage completion still goes through existing summary/finalization
- existing error handling and retry/fallback behavior remains intact
- no new persisted schema changes

### 5. Add regression tests

Update [test_executor_stage_logs.py](/Users/jowang/Documents/github/silicon_agent/platform/tests/test_executor_stage_logs.py) to cover:

- coding exploration budget breach
- test exploration budget breach
- failed test command followed by forced blocker-style convergence
- one-time forced-convergence injection
- unaffected behavior for other roles

If needed, add prompt-text expectation coverage to [test_prompts.py](/Users/jowang/Documents/github/silicon_agent/platform/tests/test_prompts.py) only when shared prompt helpers are updated.

## Verification

Run:

```bash
cd platform
. .venv/bin/activate
pytest tests/test_executor_stage_logs.py tests/test_prompts.py -q
```

If implementation reaches live validation, use a cloned VM task similar to the previous `helloworld` runs and compare:

- `llm_turn_sent`
- `tool_call_executed`
- `Max turns reached`

for `code` and `test` stages.

## Risks

- Heuristics may be too aggressive and cut off valid exploration in legitimate tasks.
- Heuristics may be too weak and not materially improve live behavior.
- Tool classification may miss edge cases where an `execute` command is exploratory vs. truly validating.

## Mitigations

- Keep the first-pass thresholds conservative.
- Inject one recovery prompt before failing or finishing, instead of immediately aborting.
- Limit the initial implementation to `coding` and `test` only.

## Rollback

Revert the executor helpers and test changes in:

- [executor.py](/Users/jowang/Documents/github/silicon_agent/platform/app/worker/executor.py)
- [test_executor_stage_logs.py](/Users/jowang/Documents/github/silicon_agent/platform/tests/test_executor_stage_logs.py)

## Exit Criteria

- Targeted tests pass.
- The new logic is isolated to executor-level behavior.
- A subsequent live validation can reasonably show lower exploration churn than the current capped-`max_turns` behavior.
