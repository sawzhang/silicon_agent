import { create } from 'zustand';
import type { WSTaskLogStreamPayload } from '@/types/websocket';

interface TaskLogStreamStore {
  subscriptions: Record<string, number>;
  linesByLog: Record<string, string[]>;
  statusByLog: Record<string, string>;
  subscribe: (logId: string) => void;
  unsubscribe: (logId: string) => void;
  clear: (logId: string) => void;
  setStatus: (logId: string, status: string) => void;
  append: (payload: WSTaskLogStreamPayload, timestamp: string) => void;
}

export const useTaskLogStreamStore = create<TaskLogStreamStore>((set, get) => ({
  subscriptions: {},
  linesByLog: {},
  statusByLog: {},
  subscribe: (logId) =>
    set((state) => ({
      subscriptions: {
        ...state.subscriptions,
        [logId]: Date.now(),
      },
      linesByLog: {
        ...state.linesByLog,
        [logId]: state.linesByLog[logId] || [],
      },
    })),
  unsubscribe: (logId) =>
    set((state) => {
      const next = { ...state.subscriptions };
      delete next[logId];
      return { subscriptions: next };
    }),
  clear: (logId) =>
    set((state) => ({
      linesByLog: {
        ...state.linesByLog,
        [logId]: [],
      },
      statusByLog: {
        ...state.statusByLog,
        [logId]: 'running',
      },
    })),
  setStatus: (logId, status) =>
    set((state) => ({
      statusByLog: {
        ...state.statusByLog,
        [logId]: status,
      },
    })),
  append: (payload, timestamp) => {
    const subscribedAt = get().subscriptions[payload.log_id];
    if (!subscribedAt) return;

    const eventTs = Number.isFinite(Date.parse(timestamp)) ? Date.parse(timestamp) : Date.now();
    if (eventTs < subscribedAt) return;

    set((state) => {
      const current = state.linesByLog[payload.log_id] || [];
      const nextLines = payload.chunk ? [...current, payload.chunk] : current;
      return {
        linesByLog: {
          ...state.linesByLog,
          [payload.log_id]: nextLines.slice(-2000),
        },
        statusByLog: {
          ...state.statusByLog,
          [payload.log_id]: payload.status || (payload.finished ? 'success' : 'running'),
        },
      };
    });
  },
}));
