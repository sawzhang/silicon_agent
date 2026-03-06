import { beforeEach, describe, expect, it } from 'vitest';
import type { WSTaskLogStreamPayload } from '@/types/websocket';
import { useTaskLogStreamStore } from './taskLogStreamStore';

function resetStreamStore() {
  useTaskLogStreamStore.setState({
    subscriptions: {},
    linesByLog: {},
    statusByLog: {},
  });
}

function makePayload(overrides: Partial<WSTaskLogStreamPayload> = {}): WSTaskLogStreamPayload {
  return {
    task_id: 'task-1',
    stage_id: 'stage-1',
    stage_name: 'coding',
    log_id: 'log-1',
    tool_call_id: 'tool-1',
    chunk: 'chunk\n',
    finished: false,
    ...overrides,
  };
}

describe('taskLogStreamStore', () => {
  beforeEach(() => {
    resetStreamStore();
  });

  it('ignores replay chunks older than the subscription timestamp', () => {
    const subscribedAt = Date.parse('2026-03-06T12:00:01Z');
    useTaskLogStreamStore.setState({
      subscriptions: { 'log-1': subscribedAt },
      linesByLog: { 'log-1': [] },
    });

    const append = useTaskLogStreamStore.getState().append;
    append(makePayload({ chunk: 'late\n' }), '2026-03-06T12:00:00Z');
    append(makePayload({ chunk: 'fresh\n' }), '2026-03-06T12:00:02Z');

    expect(useTaskLogStreamStore.getState().linesByLog['log-1']).toEqual(['fresh\n']);
    expect(useTaskLogStreamStore.getState().statusByLog['log-1']).toBe('running');
  });

  it('caps replay lines and infers success when a stream finishes without status', () => {
    const existingLines = Array.from({ length: 2000 }, (_, index) => `line-${index}\n`);
    useTaskLogStreamStore.setState({
      subscriptions: { 'log-1': 1 },
      linesByLog: { 'log-1': existingLines },
    });

    useTaskLogStreamStore
      .getState()
      .append(makePayload({ chunk: 'tail\n', finished: true, status: undefined }), '2026-03-06T12:00:02Z');

    const lines = useTaskLogStreamStore.getState().linesByLog['log-1'];
    expect(lines).toHaveLength(2000);
    expect(lines[0]).toBe('line-1\n');
    expect(lines[lines.length - 1]).toBe('tail\n');
    expect(useTaskLogStreamStore.getState().statusByLog['log-1']).toBe('success');
  });

  it('stops collecting chunks after unsubscribe', () => {
    useTaskLogStreamStore.setState({
      subscriptions: { 'log-1': Date.parse('2026-03-06T12:00:01Z') },
      linesByLog: { 'log-1': [] },
    });

    useTaskLogStreamStore.getState().unsubscribe('log-1');
    useTaskLogStreamStore
      .getState()
      .append(makePayload({ chunk: 'ignored\n' }), '2026-03-06T12:00:02Z');

    expect(useTaskLogStreamStore.getState().linesByLog['log-1']).toEqual([]);
    expect(useTaskLogStreamStore.getState().statusByLog['log-1']).toBeUndefined();
  });
});
