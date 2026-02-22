import { create } from 'zustand';

export interface Notification {
  id: string;
  type: 'gate_created' | 'gate_resolved' | 'error' | 'info';
  title: string;
  message: string;
  timestamp: string;
  read: boolean;
}

interface NotificationStore {
  notifications: Notification[];
  refreshVersion: number;
  addNotification: (notification: Notification) => void;
  markRead: (id: string) => void;
  unreadCount: () => number;
  bumpRefresh: () => void;
}

export const useNotificationStore = create<NotificationStore>((set, get) => ({
  notifications: [],
  refreshVersion: 0,
  addNotification: (notification) =>
    set((state) => ({
      notifications: [notification, ...state.notifications].slice(0, 50),
    })),
  markRead: (id) =>
    set((state) => ({
      notifications: state.notifications.map((n) =>
        n.id === id ? { ...n, read: true } : n,
      ),
    })),
  unreadCount: () => get().notifications.filter((n) => !n.read).length,
  bumpRefresh: () => set((state) => ({ refreshVersion: state.refreshVersion + 1 })),
}));
