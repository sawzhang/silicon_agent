import React from 'react';
import { Tag } from 'antd';
import { ProTable } from '@ant-design/pro-components';
import type { ProColumns } from '@ant-design/pro-components';
import { Link } from 'react-router-dom';
import { listProjectEvents } from '@/services/triggerApi';
import { formatTimestamp } from '@/utils/formatters';
import type { TriggerEvent } from '@/types/trigger';

const RESULT_CONFIG: Record<string, { color: string; label: string }> = {
  triggered: { color: 'success', label: '已触发' },
  skipped_filter: { color: 'warning', label: '过滤跳过' },
  skipped_dedup: { color: 'processing', label: '去重跳过' },
  skipped_no_rule: { color: 'default', label: '无匹配规则' },
};

const SOURCE_COLORS: Record<string, string> = {
  github: 'green',
  jira: 'blue',
  gitlab: 'orange',
  webhook: 'default',
  cron: 'purple',
};

interface Props {
  projectId: string;
}

const TriggerEventsTab: React.FC<Props> = ({ projectId }) => {
  const columns: ProColumns<TriggerEvent>[] = [
    {
      title: '来源',
      dataIndex: 'source',
      width: 100,
      render: (_, r) => <Tag color={SOURCE_COLORS[r.source] || 'default'}>{r.source}</Tag>,
    },
    { title: '事件类型', dataIndex: 'event_type', width: 160 },
    {
      title: '结果',
      dataIndex: 'result',
      width: 120,
      render: (_, r) => {
        const cfg = RESULT_CONFIG[r.result] || { color: 'default', label: r.result };
        return <Tag color={cfg.color}>{cfg.label}</Tag>;
      },
    },
    {
      title: '任务',
      dataIndex: 'task_id',
      width: 120,
      render: (_, r) =>
        r.task_id ? (
          <Link to={`/tasks/${r.task_id}`}>{r.task_id.slice(0, 8)}...</Link>
        ) : (
          '-'
        ),
    },
    {
      title: '去重键',
      dataIndex: 'dedup_key',
      ellipsis: true,
      render: (_, r) => r.dedup_key || '-',
    },
    {
      title: '时间',
      dataIndex: 'created_at',
      width: 160,
      render: (_, r) => formatTimestamp(r.created_at),
    },
  ];

  return (
    <ProTable<TriggerEvent>
      rowKey="id"
      columns={columns}
      search={false}
      request={async () => {
        const events = await listProjectEvents(projectId);
        return { data: events, total: events.length, success: true };
      }}
      pagination={{ defaultPageSize: 50 }}
    />
  );
};

export default TriggerEventsTab;
