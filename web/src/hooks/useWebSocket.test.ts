import React, { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useTaskLogStreamStore } from '@/stores/taskLogStreamStore';
import { useWebSocket } from './useWebSocket';

class MockWebSocket {
  static OPEN = 1;
  static instances: MockWebSocket[] = [];

  readonly url: string;
  readyState = MockWebSocket.OPEN;
  sent: string[] = [];
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }

  send(data: string) {
    this.sent.push(data);
  }

  close() {
    this.onclose?.({} as CloseEvent);
  }

  emitMessage(data: unknown) {
    this.onmessage?.({ data: typeof data === 'string' ? data : JSON.stringify(data) } as MessageEvent<string>);
  }
}

function resetStreamStore() {
  useTaskLogStreamStore.setState({
    subscriptions: {},
    linesByLog: {},
    statusByLog: {},
  });
}

function HookHarness() {
  useWebSocket();
  return null;
}

function renderHookHarness(root: Root, queryClient: QueryClient) {
  root.render(
    React.createElement(
      QueryClientProvider,
      { client: queryClient },
      React.createElement(HookHarness),
    ),
  );
}

describe('useWebSocket', () => {
  const OriginalWebSocket = globalThis.WebSocket;
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    MockWebSocket.instances = [];
    resetStreamStore();
    queryClient = new QueryClient();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    vi.spyOn(console, 'log').mockImplementation(() => {});
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    globalThis.WebSocket = MockWebSocket as unknown as typeof WebSocket;
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    queryClient.clear();
    container.remove();
    globalThis.WebSocket = OriginalWebSocket;
    delete (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT;
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it('routes task_log_stream messages into the stream store', () => {
    useTaskLogStreamStore.setState({
      subscriptions: { 'log-1': Date.parse('2026-03-06T12:00:00Z') },
      linesByLog: { 'log-1': [] },
    });

    act(() => {
      renderHookHarness(root, queryClient);
    });

    const ws = MockWebSocket.instances[0];
    act(() => {
      ws.emitMessage({
        type: 'task_log_stream',
        timestamp: '2026-03-06T12:00:01Z',
        payload: {
          task_id: 'task-1',
          stage_id: 'stage-1',
          stage_name: 'coding',
          log_id: 'log-1',
          tool_call_id: 'tool-1',
          chunk: 'streamed\n',
          finished: false,
        },
      });
    });

    expect(useTaskLogStreamStore.getState().linesByLog['log-1']).toEqual(['streamed\n']);
    expect(useTaskLogStreamStore.getState().statusByLog['log-1']).toBe('running');
  });

  it('does not schedule reconnect when the hook unmounts intentionally', () => {
    vi.useFakeTimers();

    act(() => {
      renderHookHarness(root, queryClient);
    });

    expect(MockWebSocket.instances).toHaveLength(1);

    act(() => {
      root.unmount();
    });

    expect(vi.getTimerCount()).toBe(0);
  });
});
