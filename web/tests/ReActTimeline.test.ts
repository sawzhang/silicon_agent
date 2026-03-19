import { describe, expect, it } from 'vitest';
import type { TaskLogEvent } from '@/services/taskLogApi';
import { getThoughtDisplay, getTurnBadges, stripMaxTurnsSentinel } from '@/components/ReActTimeline';

function makeLog(overrides: Partial<TaskLogEvent>): TaskLogEvent {
  return {
    id: 'log-1',
    task_id: 'task-1',
    stage_id: 'stage-1',
    stage_name: 'coding',
    agent_role: 'coding',
    correlation_id: 'chat-1',
    event_seq: 1,
    event_type: 'agent_runner_chat_received',
    event_source: 'llm',
    status: 'success',
    request_body: null,
    response_body: null,
    command: null,
    command_args: null,
    workspace: null,
    execution_mode: null,
    duration_ms: null,
    result: null,
    output_summary: null,
    output_truncated: false,
    missing_fields: [],
    created_at: '2026-03-19T00:00:00.000Z',
    ...overrides,
  };
}

describe('ReActTimeline helper transforms', () => {
  it('extracts continuation and forced convergence badges from task logs', () => {
    const log = makeLog({
      request_body: { continuation: 2, forced_convergence: true },
    });

    expect(getTurnBadges(log)).toEqual([
      { label: 'Continuation #2', color: 'blue' },
      { label: 'Forced Convergence', color: 'gold' },
    ]);
  });

  it('prefers response metadata when request body is absent', () => {
    const log = makeLog({
      response_body: { continuation: '1', forced_convergence: true },
    });

    expect(getTurnBadges(log)).toEqual([
      { label: 'Continuation #1', color: 'blue' },
      { label: 'Forced Convergence', color: 'gold' },
    ]);
  });

  it('strips the max-turn sentinel while preserving surrounding text', () => {
    expect(
      stripMaxTurnsSentinel(
        'alpha\n[Max turns reached. Please continue the conversation.]\nbeta'
      )
    ).toEqual({
      text: 'alpha\nbeta',
      truncated: true,
    });
  });

  it('treats sentinel-only thoughts as truncated system notes', () => {
    const thought = getThoughtDisplay(
      makeLog({
        response_body: { content: '[Max turns reached. Please continue the conversation.]' },
      })
    );

    expect(thought).toEqual({
      text: '',
      truncated: true,
      badges: [],
    });
  });
});
