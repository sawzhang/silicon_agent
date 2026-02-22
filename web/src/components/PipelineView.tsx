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

const STAGE_DISPLAY: Record<string, string> = Object.fromEntries(
  STAGE_NAMES.map((sn) => [sn.key, sn.name])
);

const PipelineView: React.FC<PipelineViewProps> = ({ stages }) => {
  const currentIndex = stages.findIndex((s) => s.status === 'running');

  if (stages.length > 0) {
    const items = stages.map((stage) => ({
      title: STAGE_DISPLAY[stage.stage_name] || stage.stage_name,
      status: STATUS_MAP[stage.status] || ('wait' as const),
      description: stage.agent_role || undefined,
    }));

    return (
      <Steps
        current={currentIndex >= 0 ? currentIndex : undefined}
        size="small"
        items={items}
        style={{ marginBottom: 24 }}
      />
    );
  }

  // Fallback: use hardcoded STAGE_NAMES when no stages data
  const fallbackItems = STAGE_NAMES.map((sn) => ({
    title: sn.name,
    status: 'wait' as const,
  }));

  return (
    <Steps
      size="small"
      items={fallbackItems}
      style={{ marginBottom: 24 }}
    />
  );
};

export default PipelineView;
