import React from 'react';
import { Timeline, Typography, Tag, Spin } from 'antd';
import { useActivityStore } from '@/stores/activityStore';
import { ROLE_DISPLAY_NAMES } from '@/utils/constants';
import { formatRelativeTime } from '@/utils/formatters';
import { useQuery } from '@tanstack/react-query';
import { listAuditLogs } from '@/services/auditApi';

const { Text } = Typography;

const LEVEL_COLORS: Record<string, string> = {
  info: 'blue',
  warning: 'orange',
  error: 'red',
  low: 'blue',
  medium: 'orange',
  high: 'red',
  critical: 'red',
};

const ActivityFeed: React.FC = () => {
  const wsActivities = useActivityStore((s) => s.activities);
  const { data: auditData, isLoading } = useQuery({
    queryKey: ['audit-recent'],
    queryFn: () => listAuditLogs({ page: 1, page_size: 20 }),
  });

  // Merge WebSocket activities with audit log history
  const auditActivities = (auditData?.logs ?? []).map((log) => ({
    id: log.id,
    role: log.role,
    action: log.action,
    detail: log.detail || '',
    timestamp: log.timestamp,
    level: log.risk_level === 'low' ? 'info' as const : log.risk_level === 'medium' ? 'warning' as const : 'error' as const,
  }));

  // WS activities first (most recent), then audit logs as history
  const allActivities = [...wsActivities, ...auditActivities.filter(
    (a) => !wsActivities.some((ws) => ws.id === a.id)
  )].slice(0, 20);

  if (isLoading) {
    return <Spin size="small" />;
  }

  if (allActivities.length === 0) {
    return <Text type="secondary">No recent activities</Text>;
  }

  return (
    <Timeline
      items={allActivities.map((a) => ({
        color: LEVEL_COLORS[a.level] || 'blue',
        children: (
          <div>
            <Tag>{ROLE_DISPLAY_NAMES[a.role] || a.role}</Tag>
            <Text>{a.action}</Text>
            <br />
            {a.detail && (
              <>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {a.detail}
                </Text>
                <br />
              </>
            )}
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
