import { create } from 'zustand';

export interface AgentState {
  role: string;
  status: 'running' | 'idle' | 'waiting' | 'error' | 'stopped';
  model: string;
  current_task_id: string | null;
  current_stage: string | null;
  error_message: string | null;
}

const DEFAULT_AGENTS: Record<string, AgentState> = {
  orchestrator: { role: 'orchestrator', status: 'stopped', model: 'claude-sonnet-4-20250514', current_task_id: null, current_stage: null, error_message: null },
  spec: { role: 'spec', status: 'stopped', model: 'claude-sonnet-4-20250514', current_task_id: null, current_stage: null, error_message: null },
  coding: { role: 'coding', status: 'stopped', model: 'claude-sonnet-4-20250514', current_task_id: null, current_stage: null, error_message: null },
  test: { role: 'test', status: 'stopped', model: 'claude-sonnet-4-20250514', current_task_id: null, current_stage: null, error_message: null },
  review: { role: 'review', status: 'stopped', model: 'claude-sonnet-4-20250514', current_task_id: null, current_stage: null, error_message: null },
  smoke: { role: 'smoke', status: 'stopped', model: 'claude-sonnet-4-20250514', current_task_id: null, current_stage: null, error_message: null },
  doc: { role: 'doc', status: 'stopped', model: 'claude-sonnet-4-20250514', current_task_id: null, current_stage: null, error_message: null },
};

interface AgentStore {
  agents: Record<string, AgentState>;
  updateAgent: (role: string, update: Partial<AgentState>) => void;
  setAgents: (agents: Record<string, AgentState>) => void;
}

export const useAgentStore = create<AgentStore>((set) => ({
  agents: { ...DEFAULT_AGENTS },
  updateAgent: (role, update) =>
    set((state) => ({
      agents: {
        ...state.agents,
        [role]: { ...state.agents[role], ...update },
      },
    })),
  setAgents: (agents) => set({ agents }),
}));
