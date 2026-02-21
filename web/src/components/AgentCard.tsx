import React from 'react';
import { Card, Badge, Tag, Typography } from 'antd';
import { RobotOutlined } from '@ant-design/icons';
import type { AgentState } from '@/stores/agentStore';
import { ROLE_DISPLAY_NAMES, STATUS_COLORS } from '@/utils/constants';

const { Text } = Typography;

const STATUS_BADGE: Record<string, 'success' | 'processing' | 'warning' | 'error' | 'default'> = {
  running: 'processing',
  idle: 'success',
  waiting: 'warning',
  error: 'error',
  stopped: 'default',
};

interface AgentCardProps {
  agent: AgentState;
}

const AgentCard: React.FC<AgentCardProps> = ({ agent }) => {
  const displayName = ROLE_DISPLAY_NAMES[agent.role] || agent.role;
  const badgeStatus = STATUS_BADGE[agent.status] || 'default';

  return (
    <Card size="small" style={{ minWidth: 160 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
        <RobotOutlined style={{ fontSize: 20 }} />
        <div>
          <Text strong>{displayName}</Text>
          <br />
          <Text type="secondary" style={{ fontSize: 12 }}>{agent.role}</Text>
        </div>
      </div>
      <div style={{ marginBottom: 4 }}>
        <Badge status={badgeStatus} text={<Tag color={STATUS_COLORS[agent.status]}>{agent.status}</Tag>} />
      </div>
      <Text type="secondary" style={{ fontSize: 12 }}>
        {agent.model}
      </Text>
      {agent.current_task_id && (
        <div style={{ marginTop: 4 }}>
          <Text type="secondary" style={{ fontSize: 12 }}>
            Task: {agent.current_task_id.slice(0, 8)}...
          </Text>
        </div>
      )}
    </Card>
  );
};

export default AgentCard;
