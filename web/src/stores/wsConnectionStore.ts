import { create } from 'zustand';

export type WSConnectionStatus = 'connected' | 'disconnected' | 'reconnecting';

interface WSConnectionStore {
  status: WSConnectionStatus;
  retryCount: number;
  setConnected: () => void;
  setDisconnected: () => void;
  setReconnecting: () => void;
}

export const useWSConnectionStore = create<WSConnectionStore>((set) => ({
  status: 'disconnected',
  retryCount: 0,
  setConnected: () => set({ status: 'connected', retryCount: 0 }),
  setDisconnected: () =>
    set((state) => ({ status: 'disconnected', retryCount: state.retryCount + 1 })),
  setReconnecting: () => set({ status: 'reconnecting' }),
}));
