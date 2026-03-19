# Stage Reset And Preflight Design

## Background

Recent task log analysis shows that extreme token usage in `coding` and `test` is dominated by repeated multi-turn ReAct loops rather than any one oversized file.

The current cost pattern comes from three factors compounding together:

1. A large fixed prompt base per stage:
   - system prompt
   - role skill directories
   - repo context
   - project memory
2. Repeated `runner.chat(..., reset=False)` continuation within the same stage.
3. Repeated exploration turns (`ls`, `find`, `read`, lightweight shell discovery) before implementation or validation.

Even after recent turn-budget and convergence work, live runs still show the model spending too many turns on exploration, while each additional turn carries increasing historical context.

## Goal

Reduce token cost and exploration churn in `coding` and `test` by implementing both:

- stage-local rolling resets with compact checkpoints
- deterministic platform-side preflight scan summaries

## Non-Goals

- No task/stage graph redesign
- No provider-specific prompt caching as the primary fix
- No changes to task log API shape
- No model routing redesign

## Why These Two Changes Together

Either change alone helps, but together they address both sides of the problem:

- rolling reset reduces repeated historical context
- preflight scan removes avoidable exploration turns

This is the strongest cost/control improvement available without changing the overall stage model.

## Recommended Design

### 1. Rolling Stage Reset With Compact Checkpoints

For `coding` and `test`, do not let one long-running stage conversation accumulate unbounded history.

Instead, after a small number of exploration or tool rounds, or after truncation pressure is observed, the executor should:

1. collect a compact checkpoint
2. restart the runner conversation with `reset=True`
3. continue from the checkpoint rather than from full raw history

The checkpoint should contain only the minimum needed state:

- current task objective
- current stage goal
- confirmed facts discovered so far
- files already changed
- latest meaningful tool results
- the immediate next required action

This preserves continuity while preventing the stage from carrying every prior prompt, tool reply, and continuation through the entire run.

### 2. Platform-Side Preflight Scan Summary

Before `coding` and `test`, the platform should run a small deterministic repo scan and inject a compact summary into the stage prompt.

This replaces a large portion of the model’s exploratory shell work.

For `coding`, preflight should gather things like:

- key package / module roots
- likely implementation entrypoints
- existing controller / handler / service examples
- common response wrapper or domain model locations
- test framework presence
- build file hints

For `test`, preflight should gather things like:

- relevant existing test files
- framework and runner clues
- the most likely target test directories
- existing test pattern examples

The preflight output should be short and structured, designed to replace multiple `find`, `ls`, and `read` rounds with one injected context block.

### 3. Executor Ownership

The changes should remain executor-driven so they apply consistently to:

- host execution
- sandbox execution

The stage executor should become responsible for:

- deciding when a stage conversation has accumulated too much churn
- building the compact checkpoint
- restarting the stage chat cleanly

The repo scan should be generated before stage execution and passed in as a small extra context block, similar to how repo context and project memory are already injected today.

### 4. Minimal Context Surface

This design should avoid adding yet another large context block.

The preflight summary should therefore be:

- tightly capped
- role-specific
- intentionally factual rather than verbose

Likewise, checkpoint summaries should be much smaller than carrying the full multi-turn history forward.

## Alternatives Considered

### 1. Prompt Caching First

This helps billing for repeated static prompt prefixes, but it does not solve the growing-history problem. It is still useful later, but it should not be the first or only fix.

### 2. Lower `max_turns` Further

This constrains runaway behavior, but it does not ensure that the remaining turns are spent effectively.

### 3. Prompt-Only “Use Fewer Bash Calls”

This is helpful as a guardrail, but not reliable enough as the main control. Deterministic preflight is more stable than asking the model to be disciplined.

## Data Flow

### Coding

1. Engine prepares repo context and project memory as today.
2. New coding preflight scan runs and produces compact summary text.
3. Executor starts coding stage with the summary included.
4. If the stage begins to accumulate too many exploration/tool rounds, executor builds a checkpoint and re-enters with a fresh chat.
5. The stage proceeds from compressed current state rather than full historical turns.

### Test

1. Engine prepares existing compressed prior outputs as today.
2. New test preflight scan runs and provides framework/test-location summary.
3. Executor starts test stage with the summary included.
4. If test churn accumulates, executor rebuilds the stage from a compact checkpoint.
5. Validation continues from current facts instead of raw conversation history.

## Error Handling

If preflight scan fails:

- stage should continue without it
- failure should be logged
- no task failure should occur solely because preflight was unavailable

If rolling reset checkpoint generation fails:

- stage should fall back to current behavior
- failure should be logged
- existing stage lifecycle semantics should remain intact

## Testing Strategy

Add focused coverage for:

- coding preflight summary generation
- test preflight summary generation
- executor restart path after exploration churn
- checkpoint prompt includes only reduced current-state data
- non-target roles remain unchanged

## Success Criteria

Compared to current live baselines, successful improvement should show:

- fewer exploration tool calls before implementation
- fewer continuation rounds
- materially lower total tokens in `coding` and `test`
- fewer repeated `Max turns reached` events

## Rollout Order

Recommended rollout sequence:

1. implement preflight summary generation
2. implement rolling reset/checkpoint logic
3. validate on the same VM task family currently used for comparison
4. only after that, decide whether prompt caching is still worth prioritizing
