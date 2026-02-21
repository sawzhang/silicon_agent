import React from 'react';
import { Timeline, Typography, Tag } from 'antd';
import { useActivityStore } from '@/stores/activityStore';
import { ROLE_DISPLAY_NAMES } from '@/utils/constants';
import { formatRelativeTime } from '@/utils/formatters';

const { Text } = Typography;

const LEVEL_COLORS: Record<string, string> = {
  info: 'blue',
  warning: 'orange',
  error: 'red',
};

const ActivityFeed: React.FC = () => {
  const activities = useActivityStore((s) => s.activities);

  if (activities.length === 0) {
    return <Text type="secondary">No recent activities</Text>;
  }

  return (
    <Timeline
      items={activities.slice(0, 20).map((a) => ({
        color: LEVEL_COLORS[a.level] || 'blue',
        children: (
          <div>
            <Tag>{ROLE_DISPLAY_NAMES[a.role] || a.role}</Tag>
            <Text>{a.action}</Text>
            <br />
            <Text type="secondary" style={{ fontSize: 12 }}>
              {a.detail}
            </Text>
            <br />
            <Text type="secondary" style={{ fontSize: 11 }}>
              {formatRelativeTime(a.timestamp)}
            </Text>
          </div>
        ),
      }))}
    />
  );
};

export default ActivityFeed;
