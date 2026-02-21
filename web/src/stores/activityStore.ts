import { create } from 'zustand';

export interface Activity {
  id: string;
  role: string;
  action: string;
  detail: string;
  timestamp: string;
  level: 'info' | 'warning' | 'error';
}

interface ActivityStore {
  activities: Activity[];
  addActivity: (activity: Activity) => void;
}

export const useActivityStore = create<ActivityStore>((set) => ({
  activities: [],
  addActivity: (activity) =>
    set((state) => ({
      activities: [activity, ...state.activities].slice(0, 100),
    })),
}));
