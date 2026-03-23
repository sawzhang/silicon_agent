import { create } from 'zustand';

export interface AgentState {
  role: string;
  status: 'running' | 'idle' | 'waiting' | 'error' | 'stopped';
  model: string;
  current_task_id: string | null;
  current_stage: string | null;
  error_message: string | null;
}

function createDefaultAgent(role: string): AgentState {
  return {
    role,
    status: 'stopped',
    model: '未配置',
    current_task_id: null,
    current_stage: null,
    error_message: null,
  };
}

const DEFAULT_AGENTS: Record<string, AgentState> = {
  orchestrator: createDefaultAgent('orchestrator'),
  spec: createDefaultAgent('spec'),
  coding: createDefaultAgent('coding'),
  test: createDefaultAgent('test'),
  review: createDefaultAgent('review'),
  smoke: createDefaultAgent('smoke'),
  doc: createDefaultAgent('doc'),
  'dispatch issue': createDefaultAgent('dispatch issue'),
  'des encrypt': createDefaultAgent('des encrypt'),
};

interface AgentStore {
  agents: Record<string, AgentState>;
  updateAgent: (role: string, update: Partial<AgentState>) => void;
  setAgents: (agents: Record<string, AgentState>) => void;
}

export const useAgentStore = create<AgentStore>((set) => ({
  agents: { ...DEFAULT_AGENTS },
  updateAgent: (role, update) =>
    set((state) => {
      const normalizedRole = role?.trim();
      if (!normalizedRole) {
        return state;
      }

      const current = state.agents[normalizedRole] ?? createDefaultAgent(normalizedRole);
      return {
        agents: {
          ...state.agents,
          [normalizedRole]: { ...current, ...update, role: normalizedRole },
        },
      };
    }),
  setAgents: (agents) => set({ agents }),
}));
