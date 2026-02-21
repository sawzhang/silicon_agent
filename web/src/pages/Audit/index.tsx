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

const AuditPage: React.FC = () => {
  const columns: ProColumns<AuditLog>[] = [
    {
      title: 'Timestamp',
      dataIndex: 'timestamp',
      valueType: 'dateRange',
      render: (_, record) => formatTimestamp(record.timestamp),
      width: 180,
    },
    {
      title: 'Role',
      dataIndex: 'role',
      valueEnum: Object.fromEntries(
        Object.entries(ROLE_DISPLAY_NAMES).map(([k, v]) => [k, v]),
      ),
      render: (_, record) => <Tag>{ROLE_DISPLAY_NAMES[record.role] || record.role}</Tag>,
      width: 100,
    },
    {
      title: 'Action',
      dataIndex: 'action',
      ellipsis: true,
    },
    {
      title: 'Detail',
      dataIndex: 'detail',
      ellipsis: true,
      search: false,
    },
    {
      title: 'Risk Level',
      dataIndex: 'risk_level',
      valueEnum: { low: 'Low', medium: 'Medium', high: 'High', critical: 'Critical' },
      render: (_, record) => <Tag color={RISK_COLOR[record.risk_level]}>{record.risk_level}</Tag>,
      width: 100,
    },
    {
      title: 'Task ID',
      dataIndex: 'task_id',
      search: false,
      render: (_, record) => record.task_id ? record.task_id.slice(0, 8) + '...' : '-',
      width: 100,
    },
  ];

  return (
    <ProTable<AuditLog>
      headerTitle="Audit Logs"
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
