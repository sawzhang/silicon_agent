# Coding And Testing Convergence Design

**Date:** 2026-03-19

**Problem**

Simple tasks are spending too many turns in the `coding` and `test` stages. The current behavior allows broad repository exploration, and when the model hits the turn limit the continuation prompt is too weak to force the stage back toward concrete actions. In practice this leads to repeated `read/ls/find` calls, continuation loops, and slow delivery even when the requested change is small.

**Goal**

Improve convergence for all `coding` and `test` stage executions with the smallest possible execution-chain change. The design should avoid changes to stage orchestration, model routing, sandbox selection, or task lifecycle. The work should only tighten stage instructions and continuation behavior.

**Non-Goals**

- Do not change task templates or stage topology.
- Do not add new execution modes or sandbox backends.
- Do not introduce repository-specific heuristics such as “simple task mode”.
- Do not add exploration budgets, tool counters, or runtime interruption logic in this iteration.
- Do not change non-`coding` / non-`test` stages unless required for shared helper plumbing.

**Current State**

- Stage prompts are built in [prompts.py](/Users/jowang/Documents/github/silicon_agent/platform/app/worker/prompts.py).
- In-process continuation handling lives in [executor.py](/Users/jowang/Documents/github/silicon_agent/platform/app/worker/executor.py).
- `coding` and `test` already have stage guardrails, but they focus on stage boundaries rather than convergence.
- Continuation prompts currently say only “请继续完成上面的输出，从你停下的地方继续。”, which encourages more prose instead of concrete progress.

**Constraints**

- Keep the implementation localized to prompt and continuation layers.
- Preserve compatibility with existing event logging and continuation flow.
- Keep Chinese prompt style consistent with the rest of the worker prompts.
- Avoid changing default max-turn behavior in this iteration.

**Approach Options**

1. Guardrails only
Add stricter `coding` and `test` guardrails in `prompts.py`.

Pros:
- Smallest code diff.
- No helper changes.

Cons:
- Only affects the first prompt.
- Does not solve continuation loops directly.

2. Guardrails plus stage-specific continuation prompts
Strengthen `coding` and `test` stage guardrails and replace the generic continuation prompt with stage-specific convergence prompts.

Pros:
- Directly addresses the observed failure mode.
- Still limited to prompt-generation and continuation code.
- No model or orchestration changes.

Cons:
- Slightly larger change surface than guardrails alone.

3. Guardrails plus continuation prompts plus lower max turns
Do option 2 and also reduce `coding` / `test` max-turn ceilings.

Pros:
- More aggressive convergence.

Cons:
- Higher risk of hurting legitimate longer tasks.
- Harder to tune safely without broader validation.

**Recommendation**

Choose option 2.

It is the best fit for the stated goal: stronger convergence for all `coding` and `test` stages with minimal execution-chain change. It improves both the initial stage instruction and the continuation loop without changing orchestration or routing.

**Design**

### 1. Tighten `coding` stage guardrail

Update the `code` entry in `STAGE_GUARDRAILS` so that it explicitly instructs the agent to:

- avoid broad repository exploration;
- use already available context first;
- read more files only when a missing detail blocks implementation;
- move quickly to concrete file edits;
- run the smallest necessary validation;
- stop after implementation and a brief summary.

The guardrail should discourage “understand the whole repo first” behavior and push the agent toward the minimum set of reads needed to safely modify code.

### 2. Tighten `test` stage guardrail

Update the `test` entry in `STAGE_GUARDRAILS` so that it explicitly instructs the agent to:

- focus only on validation directly tied to the current change;
- prefer the fastest relevant verification path;
- stop once acceptance is sufficiently proven;
- avoid expanding into smoke/E2E/performance work unless explicitly requested;
- report the concrete blocker if validation cannot proceed.

This keeps the `test` stage from growing into an open-ended general validation phase.

### 3. Replace generic continuation prompts with stage-specific convergence prompts

Update `_handle_continuations()` in `executor.py` so that:

- `coding` continuations tell the agent to stop broad exploration, use the information already gathered, and produce concrete edits or a single evidenced blocker;
- `test` continuations tell the agent to stop expanding coverage, run the smallest relevant validation, and return concrete results or a blocker;
- other stages can keep the generic continuation wording, or use a neutral fallback prompt.

The continuation prompt should be action-oriented. Its purpose is not to continue reasoning indefinitely; it is to force the stage back toward a terminating action.

**Data Flow**

1. `build_user_prompt()` continues to assemble the stage prompt as today.
2. `coding` / `test` guardrails now embed convergence-specific instructions.
3. If the model returns the truncation sentinel, `_handle_continuations()` chooses a stage-aware prompt.
4. The runner continues with `reset=False`, but the continuation now carries explicit instructions to finish implementation or verification instead of continuing exploratory dialogue.

**Error Handling**

- Existing continuation retry and logging behavior remains unchanged.
- If a continuation still fails or times out, the current stage error handling path stays in place.
- No changes are needed to fallback logic, task logs schema, or retry scheduling.

**Testing Strategy**

Add focused tests only.

- Prompt tests in [test_prompts.py](/Users/jowang/Documents/github/silicon_agent/platform/tests/test_prompts.py):
  - verify `coding` guardrail includes the new convergence instructions;
  - verify `test` guardrail includes the new minimal-validation instructions.
- Executor tests in [test_executor_stage_logs.py](/Users/jowang/Documents/github/silicon_agent/platform/tests/test_executor_stage_logs.py) or a nearby executor-focused test module:
  - verify `coding` continuation uses the stage-specific prompt;
  - verify `test` continuation uses the stage-specific prompt;
  - verify non-target stages still use the generic fallback prompt.

**Risks**

- Over-constraining `coding` may reduce necessary repo discovery for legitimately complex changes.
- Over-constraining `test` may cause the agent to stop too early if prompts are too absolute.
- Prompt wording that is too long may dilute the core instruction.

**Mitigations**

- Phrase the new guardrails as “use available context first” rather than “never explore”.
- Allow one additional critical file lookup in continuation prompts when truly needed.
- Leave max-turn settings unchanged for now to isolate the impact of prompt changes.

**Success Criteria**

- `coding` stage continuations stop looping on generic prose and push toward edits or a blocker.
- `test` stage continuations stop expanding test scope after sufficient evidence is available.
- The change is limited to prompt and continuation logic.
- Existing worker flow, logging, and task lifecycle remain unchanged.
