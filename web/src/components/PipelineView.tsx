import React from 'react';
import { Steps } from 'antd';
import type { TaskStage } from '@/types/task';
import { STAGE_NAMES } from '@/utils/constants';

interface PipelineViewProps {
  stages: TaskStage[];
}

const STATUS_MAP: Record<string, 'wait' | 'process' | 'finish' | 'error'> = {
  pending: 'wait',
  running: 'process',
  completed: 'finish',
  failed: 'error',
  skipped: 'wait',
};

const PipelineView: React.FC<PipelineViewProps> = ({ stages }) => {
  const currentIndex = stages.findIndex((s) => s.status === 'running');

  const items = STAGE_NAMES.map((sn) => {
    const stage = stages.find((s) => s.name === sn.key);
    return {
      title: sn.name,
      status: stage ? STATUS_MAP[stage.status] : ('wait' as const),
      description: stage?.agent_role || undefined,
    };
  });

  return (
    <Steps
      current={currentIndex >= 0 ? currentIndex : undefined}
      size="small"
      items={items}
      style={{ marginBottom: 24 }}
    />
  );
};

export default PipelineView;
