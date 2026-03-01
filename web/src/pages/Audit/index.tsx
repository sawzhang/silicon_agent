import React from 'react';
import { Tag } from 'antd';
import { ProTable } from '@ant-design/pro-components';
import type { ProColumns } from '@ant-design/pro-components';
import { listAuditLogs } from '@/services/auditApi';
import type { AuditLog } from '@/services/auditApi';
import { formatTimestamp } from '@/utils/formatters';
import { ROLE_DISPLAY_NAMES } from '@/utils/constants';

const RISK_COLOR: Record<string, string> = {
  low: 'green',
  medium: 'blue',
  high: 'orange',
  critical: 'red',
};

const RISK_LABEL: Record<string, string> = {
  low: '低',
  medium: '中',
  high: '高',
  critical: '严重',
};

const AuditPage: React.FC = () => {
  const columns: ProColumns<AuditLog>[] = [
    {
      title: '时间戳',
      dataIndex: 'timestamp',
      valueType: 'dateRange',
      render: (_, record) => formatTimestamp(record.timestamp),
      width: 180,
    },
    {
      title: '角色',
      dataIndex: 'role',
      valueEnum: Object.fromEntries(
        Object.entries(ROLE_DISPLAY_NAMES).map(([k, v]) => [k, v]),
      ),
      render: (_, record) => <Tag>{ROLE_DISPLAY_NAMES[record.role] || record.role}</Tag>,
      width: 100,
    },
    {
      title: '动作',
      dataIndex: 'action',
      ellipsis: true,
    },
    {
      title: '详情',
      dataIndex: 'detail',
      ellipsis: true,
      search: false,
    },
    {
      title: '风险等级',
      dataIndex: 'risk_level',
      valueEnum: { low: '低', medium: '中', high: '高', critical: '严重' },
      render: (_, record) => <Tag color={RISK_COLOR[record.risk_level]}>{RISK_LABEL[record.risk_level] || record.risk_level}</Tag>,
      width: 100,
    },
    {
      title: '任务 ID',
      dataIndex: 'task_id',
      search: false,
      render: (_, record) => record.task_id ? record.task_id.slice(0, 8) + '...' : '-',
      width: 100,
    },
  ];

  return (
    <ProTable<AuditLog>
      headerTitle="审计日志"
      rowKey="id"
      columns={columns}
      request={async (params) => {
        const res = await listAuditLogs({
          role: params.role,
          risk_level: params.risk_level,
          page: params.current,
          page_size: params.pageSize,
        });
        return { data: res.logs, total: res.total, success: true };
      }}
      pagination={{ defaultPageSize: 20 }}
    />
  );
};

export default AuditPage;
