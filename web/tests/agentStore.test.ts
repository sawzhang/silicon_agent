import { beforeEach, describe, expect, it } from 'vitest';
import { useAgentStore } from '@/stores/agentStore';

describe('agentStore', () => {
  beforeEach(() => {
    useAgentStore.setState({
      agents: {
        orchestrator: {
          role: 'orchestrator',
          status: 'stopped',
          model: '未配置',
          current_task_id: null,
          current_stage: null,
          error_message: null,
        },
        spec: {
          role: 'spec',
          status: 'stopped',
          model: '未配置',
          current_task_id: null,
          current_stage: null,
          error_message: null,
        },
        coding: {
          role: 'coding',
          status: 'stopped',
          model: '未配置',
          current_task_id: null,
          current_stage: null,
          error_message: null,
        },
        test: {
          role: 'test',
          status: 'stopped',
          model: '未配置',
          current_task_id: null,
          current_stage: null,
          error_message: null,
        },
        review: {
          role: 'review',
          status: 'stopped',
          model: '未配置',
          current_task_id: null,
          current_stage: null,
          error_message: null,
        },
        smoke: {
          role: 'smoke',
          status: 'stopped',
          model: '未配置',
          current_task_id: null,
          current_stage: null,
          error_message: null,
        },
        doc: {
          role: 'doc',
          status: 'stopped',
          model: '未配置',
          current_task_id: null,
          current_stage: null,
          error_message: null,
        },
        'dispatch issue': {
          role: 'dispatch issue',
          status: 'stopped',
          model: '未配置',
          current_task_id: null,
          current_stage: null,
          error_message: null,
        },
        'des encrypt': {
          role: 'des encrypt',
          status: 'stopped',
          model: '未配置',
          current_task_id: null,
          current_stage: null,
          error_message: null,
        },
      },
    });
  });

  it('ships defaults for github issue roles', () => {
    const agents = useAgentStore.getState().agents;

    expect(agents['dispatch issue']).toMatchObject({
      role: 'dispatch issue',
      status: 'stopped',
    });
    expect(agents['des encrypt']).toMatchObject({
      role: 'des encrypt',
      status: 'stopped',
    });
  });

  it('ignores empty role updates', () => {
    const before = useAgentStore.getState().agents;

    useAgentStore.getState().updateAgent('', { status: 'running' });
    useAgentStore.getState().updateAgent('   ', { status: 'running' });

    expect(useAgentStore.getState().agents).toEqual(before);
  });

  it('creates a safe placeholder for unexpected roles', () => {
    useAgentStore.getState().updateAgent('legacy agent', { status: 'idle' });

    expect(useAgentStore.getState().agents['legacy agent']).toMatchObject({
      role: 'legacy agent',
      status: 'idle',
      model: '未配置',
    });
  });
});
