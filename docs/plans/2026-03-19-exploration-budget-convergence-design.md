# Exploration Budget Convergence Design

## Background

Recent live validation on VM shows that tightening `coding` and `test` stage guardrails plus lowering `max_turns` improved behavior, but did not remove the root failure mode:

- `coding` still spends most turns on repository exploration (`read/find/ls/cat`) before acting.
- `test` can still drift into explanation-only output after verification failures.
- `max_turns` now bounds damage, but it does not prevent the turns from being spent on the wrong behavior.

The goal of this design is to add a stronger convergence mechanism with minimal chain changes. We will keep the existing stage model, sandbox model, and AgentRunner integration intact, and only strengthen the executor behavior for `coding` and `test`.

## Goals

- Reduce wasted `coding` turns spent on exploration before code edits.
- Reduce `test` drift after failed validation commands.
- Preserve the current task/stage architecture and runtime interfaces.
- Apply consistently to host execution and sandbox execution.

## Non-Goals

- No new stages such as `explore` or `verify`.
- No AgentRunner API changes.
- No sandbox protocol changes.
- No model-routing redesign.

## Recommended Approach

Use executor-level exploration budgets with a single forced-convergence prompt.

### Why this approach

This is the smallest effective change that targets the actual failure mode. The system already captures stage events and tool-call lifecycle data inside the executor. Instead of only changing prompts or reducing `max_turns`, we can observe repeated exploration behavior and intervene once, at the executor layer, before the stage fully degenerates.

This keeps the architecture stable while giving the runtime one stronger lever than prompt text alone.

## Alternatives Considered

### 1. Lower `max_turns` further

This is easy, but it does not solve the root issue. The model can still waste the smaller budget on exploration and then get truncated earlier.

### 2. Split `coding` and `test` into sub-phases

This would likely be more effective, but it changes stage orchestration and increases behavioral complexity. It is larger than needed for the immediate problem.

### 3. Add tool-level hard blocking in AgentRunner

This could be very strong, but it requires changes below the executor boundary and is not the minimal-path solution.

## Design

### 1. Exploration budget tracking

Inside the executor, track lightweight exploration signals for `coding` and `test`:

- read-like tool usage
- directory/list/search style commands
- repeated tool-only turns without implementation or verification progress

The budget should stay intentionally simple and heuristic-based. The goal is not perfect classification. The goal is to catch obvious drift.

Suggested first-pass behavior:

- `coding`: trigger after too many exploration actions in the same stage before meaningful implementation progress
- `test`: trigger after too many exploration actions, or after failed validation attempts followed by further drift

### 2. Forced-convergence prompt

When the budget is exceeded, inject one explicit recovery prompt.

For `coding`, the prompt should require:

- stop exploring
- directly modify files
- run only minimal verification
- if still blocked, report the single blocker clearly

For `test`, the prompt should require:

- stop expanding the test surface
- give the smallest relevant validation result
- if a command failed, report the failed command, key error, and blocker
- do not declare success based only on code inspection

This should happen once per stage execution, not repeatedly.

### 3. No repeated soft looping

After a forced-convergence prompt has been issued, the executor should not continue to allow the same stage to loop through broad exploration again. The intention is:

- one normal execution window
- one forced convergence recovery chance
- then end based on the resulting output or failure

This avoids replacing one loop with another.

### 4. Minimal runtime surface area

The behavior should live in `executor.py` so it applies uniformly to:

- host/in-process task execution
- sandboxed task execution

The prompt texts may remain in executor helper functions rather than introducing a larger new prompt framework.

## Error Handling

If budget tracking cannot confidently classify a tool action, it should ignore it rather than overreact.

If the forced-convergence prompt itself fails, the stage should continue to use the current lifecycle behavior and surface the latest failure normally. The new logic should not hide existing error messages.

## Testing Strategy

Add focused executor tests that cover:

- exploration budget exceeded in `coding` triggers the forced-convergence path
- exploration budget exceeded in `test` triggers the correct test-specific prompt
- failed test-command flow requires blocker-style follow-up rather than success-style summary
- only one forced-convergence injection happens per stage
- non-target roles are unaffected

## Success Criteria

Live task behavior should improve in these ways:

- fewer `llm_turn_sent` / `tool_call_executed` events before code changes or validation
- fewer `Max turns reached` events in `coding` and `test`
- fewer `test` outputs that claim success after command failure without citing blockers

## Rollout Notes

This should ship behind existing behavior with no config migration. If needed, thresholds can remain hard-coded for the first version and be externalized later only if real usage shows that tuning is necessary.
