# Static Context Token Optimization Design

## Background

Recent live task analysis confirms that high token cost is still dominated by repeated stage-local chat calls, especially in `code` and `test`.

We have already improved behavior in three ways:

- stronger `coding` / `test` convergence guardrails
- exploration budgets and forced convergence
- preflight summaries plus checkpoint-style restart prompts

Those changes reduced some waste, but they did not fully remove the biggest fixed cost: each stage still repeatedly carries a large static prompt base.

Today that base includes some combination of:

- system prompt
- role skill directories and tool schema exposure
- repo context
- project memory
- prior stage outputs

As a result, even when the agent is doing the right kind of work, each extra turn still re-pays too much prompt cost.

## Goal

Reduce token consumption primarily by shrinking repeated static prompt/context overhead before investing in provider-specific prompt caching.

## Non-Goals

- Do not redesign the task/stage graph
- Do not replace the current AgentRunner stack
- Do not make prompt caching the immediate primary fix
- Do not remove the existing preflight or restart work

## Key Observation

The next best savings are not from forcing the model to be “more disciplined” in the abstract. They come from making each stage carry less static baggage per turn.

That means the platform should prioritize:

1. smaller models for low-complexity stages
2. fewer tools and skills exposed per stage
3. less injected repo and memory context where preflight already covers the need
4. making restart/checkpoint execution the preferred continuation path

## Recommended Approach

### Phase 1: Cheap, Low-Risk Static Context Reduction

Implement four low-risk optimizations first.

#### 1. Role-Based Model Routing

Use the existing `LLM_ROLE_MODEL_MAP` support more aggressively.

Recommended default direction:

- `parse` → lighter text/tool-capable model
- `code` → strongest coding/reasoning model
- `test` → lighter model unless the task or template explicitly requests stronger reasoning
- `signoff` → lighter text-oriented model

This does not reduce token count directly, but it reduces cost immediately and aligns model strength with stage complexity.

#### 2. Make `signoff` Text-Only

`signoff` should stop re-entering tool-driven exploration by default.

It should instead rely on:

- prior stage outputs
- structured summaries
- the latest verified results already produced by earlier stages

This reduces unnecessary tool schema exposure and avoids another mini ReAct loop at the end of the task.

#### 3. Per-Stage Tool / Skill Pruning

The current role defaults still expose too much shared capability in later-stage prompts.

We should trim stage exposure so the agent only receives the tools and skill directories it realistically needs:

- `code` should keep the core file and execution tools, but not unrelated later-stage abilities
- `test` should focus on read/edit/execute verification tools
- `signoff` should default to no tools

This reduces prompt bloat and narrows action space.

#### 4. Shrink `repo_context` and `project_memory` for `code` / `test`

Now that deterministic preflight summaries exist, `code` and `test` no longer need the full original repo-context payload on every turn.

We should split context into:

- broad repo context for earlier planning stages
- slim execution context for `code` / `test`

For execution stages, the injected context should favor:

- concise stack/build facts
- minimal path hints
- short relevant memory excerpts

and avoid re-sending large directory trees or verbose historical notes when preflight already covers the local execution target.

### Phase 2: Make Checkpoint Restart the Main Continuation Strategy

We already introduced restart/checkpoint machinery, but it should evolve from a rescue path into the preferred continuation model for `code` / `test`.

The continuation path should increasingly favor:

- `reset=True`
- a compact checkpoint prompt
- only the immediate execution state

and increasingly avoid:

- replaying full `repo_context`
- replaying full `project_memory`
- replaying large prior output blocks

The restart payload should be limited to:

- task objective
- stage goal
- preflight summary
- already confirmed edits or findings
- last 2 to 3 meaningful tool results
- immediate next action

### Phase 3: Provider-Aware Prompt Caching

Prompt caching is still worth evaluating, but only after the fixed prompt base is made smaller and more stable.

Reasons not to lead with it:

- provider support may vary across current model paths
- it does not solve growing-history behavior by itself
- it is more invasive than the earlier fixes

Once Phases 1 and 2 land, caching can be evaluated on a cleaner and more stable prompt shape.

## Alternatives Considered

### 1. Prompt Caching First

Useful later, but not the best immediate step because it does not reduce prompt size or continuation churn on its own.

### 2. More Aggressive `max_turns` Reduction

Helps cap damage, but still allows the remaining turns to carry the same large static prefix.

### 3. Prompting the Agent to Batch Shell Commands

Helpful as a guardrail, but weaker than platform-side prompt/context reduction. It depends on model compliance and does not address repeated schema/context cost.

## Data Flow Changes

### Parse / Spec-Like Stages

These stages may continue to receive broader repo context because they are responsible for planning and synthesis.

### Code / Test Stages

These stages should increasingly receive:

- slim repo facts
- short role-specific memory
- deterministic preflight summary
- compact restart checkpoints on continuation

instead of the current larger blended context shape.

### Signoff Stage

This stage should default to text-only summary and evaluation behavior, without tool re-entry.

## Testing Strategy

Add or extend focused coverage for:

- role-model resolution per stage
- signoff text-only routing
- per-role skill/tool narrowing
- prompt construction with slim execution-stage context
- restart/checkpoint payload staying compact
- non-execution stages still receiving the broader context they need

## Success Criteria

Compared to current baselines, success should show:

- lower total tokens in `code` and `test`
- fewer repeated large chat payloads
- fewer tool-driven loops in `signoff`
- lower average cost per task even before prompt caching

## Rollout Order

1. tighten model routing defaults
2. make `signoff` text-only
3. prune per-stage tool/skill exposure
4. slim `repo_context` / `project_memory` for execution stages
5. promote compact checkpoint restart into the main continuation path
6. re-measure on the same VM task family
7. only then decide whether prompt caching should be the next investment
