import React, { useState } from 'react';
import { Row, Col, Typography, Spin, Card, Empty, Statistic, Table, Tag, Button, Space } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useNavigate } from 'react-router-dom';
import { useCockpit } from '@/hooks/useKPI';
import { approveGate, rejectGate } from '@/services/gateApi';
import { useQueryClient } from '@tanstack/react-query';
import GateApprovalCard from '@/components/GateApprovalCard';
import { formatTokens, formatCost, formatRelativeTime } from '@/utils/formatters';
import type { CockpitTaskItem } from '@/types/kpi';

const { Title, Text } = Typography;

const CockpitPage: React.FC = () => {
  const { data, isLoading } = useCockpit();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [gateLoading, setGateLoading] = useState<string | null>(null);

  const handleApprove = async (id: string, comment?: string) => {
    setGateLoading(id);
    try {
      await approveGate(id, { reviewer: 'cockpit', comment });
      queryClient.invalidateQueries({ queryKey: ['cockpit'] });
    } finally {
      setGateLoading(null);
    }
  };

  const handleReject = async (id: string, comment: string) => {
    setGateLoading(id);
    try {
      await rejectGate(id, { reviewer: 'cockpit', comment });
      queryClient.invalidateQueries({ queryKey: ['cockpit'] });
    } finally {
      setGateLoading(null);
    }
  };

  const runningColumns: ColumnsType<CockpitTaskItem> = [
    {
      title: 'Title',
      dataIndex: 'title',
      key: 'title',
      ellipsis: true,
      render: (text: string, record) => (
        <a onClick={() => navigate(`/tasks/${record.id}`)}>{text}</a>
      ),
    },
    { title: 'Project', dataIndex: 'project_name', key: 'project_name', width: 120, render: (v: string | null) => v ?? '-' },
    {
      title: 'Current Stage',
      dataIndex: 'current_stage',
      key: 'current_stage',
      width: 130,
      render: (v: string | null) => v ? <Tag color="processing">{v}</Tag> : '-',
    },
    {
      title: 'Tokens',
      dataIndex: 'total_tokens',
      key: 'total_tokens',
      width: 100,
      render: (v: number) => formatTokens(v),
    },
  ];

  const failedColumns: ColumnsType<CockpitTaskItem> = [
    {
      title: 'Title',
      dataIndex: 'title',
      key: 'title',
      ellipsis: true,
      render: (text: string, record) => (
        <a onClick={() => navigate(`/tasks/${record.id}`)}>{text}</a>
      ),
    },
    {
      title: 'Error',
      dataIndex: 'error_message',
      key: 'error_message',
      ellipsis: true,
      render: (v: string | null) => v ? <Text type="danger">{v}</Text> : '-',
    },
    {
      title: 'Cost',
      dataIndex: 'total_cost_rmb',
      key: 'total_cost_rmb',
      width: 90,
      render: (v: number) => formatCost(v),
    },
    {
      title: '',
      key: 'action',
      width: 70,
      render: (_: unknown, record) => (
        <Button size="small" onClick={() => navigate(`/tasks/${record.id}`)}>
          View
        </Button>
      ),
    },
  ];

  const completedColumns: ColumnsType<CockpitTaskItem> = [
    {
      title: 'Title',
      dataIndex: 'title',
      key: 'title',
      ellipsis: true,
      render: (text: string, record) => (
        <a onClick={() => navigate(`/tasks/${record.id}`)}>{text}</a>
      ),
    },
    {
      title: 'Completed',
      dataIndex: 'completed_at',
      key: 'completed_at',
      width: 120,
      render: (v: string | null) => v ? formatRelativeTime(v) : '-',
    },
    {
      title: 'Tokens',
      dataIndex: 'total_tokens',
      key: 'total_tokens',
      width: 100,
      render: (v: number) => formatTokens(v),
    },
    {
      title: 'Cost',
      dataIndex: 'total_cost_rmb',
      key: 'total_cost_rmb',
      width: 90,
      render: (v: number) => formatCost(v),
    },
  ];

  return (
    <div>
      <Title level={4} style={{ marginBottom: 16 }}>Developer Cockpit</Title>

      {isLoading ? (
        <Spin size="large" style={{ display: 'block', margin: '40px auto' }} />
      ) : data ? (
        <>
          <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
            <Col xs={12} sm={6}>
              <Card size="small">
                <Statistic
                  title="Pending Gates"
                  value={data.pending_gates_count}
                  valueStyle={data.pending_gates_count > 0 ? { color: '#fa8c16' } : undefined}
                />
              </Card>
            </Col>
            <Col xs={12} sm={6}>
              <Card size="small">
                <Statistic
                  title="Running Tasks"
                  value={data.running_tasks_count}
                  valueStyle={{ color: '#1890ff' }}
                />
              </Card>
            </Col>
            <Col xs={12} sm={6}>
              <Card size="small">
                <Statistic
                  title="Failed Today"
                  value={data.failed_tasks_today}
                  valueStyle={data.failed_tasks_today > 0 ? { color: '#cf1322' } : undefined}
                />
              </Card>
            </Col>
            <Col xs={12} sm={6}>
              <Card size="small">
                <Statistic
                  title="Completed Today"
                  value={data.completed_tasks_today}
                  valueStyle={{ color: '#3f8600' }}
                />
              </Card>
            </Col>
          </Row>

          {/* Pending Gates */}
          <Card
            title={<Space>Pending Gates <Tag color="orange">{data.pending_gates_count}</Tag></Space>}
            style={{ marginBottom: 24 }}
          >
            {data.pending_gates.length > 0 ? (
              <Row gutter={[16, 16]}>
                {data.pending_gates.map((gate) => (
                  <Col key={gate.id} xs={24} sm={12} md={8}>
                    <GateApprovalCard
                      gate={gate}
                      onApprove={handleApprove}
                      onReject={handleReject}
                      loading={gateLoading === gate.id}
                    />
                  </Col>
                ))}
              </Row>
            ) : (
              <Empty description="No pending gates" image={Empty.PRESENTED_IMAGE_SIMPLE} />
            )}
          </Card>

          {/* Running Tasks */}
          <Card
            title={<Space>Running Tasks <Tag color="blue">{data.running_tasks_count}</Tag></Space>}
            style={{ marginBottom: 24 }}
          >
            {data.running_tasks.length > 0 ? (
              <Table<CockpitTaskItem>
                dataSource={data.running_tasks}
                columns={runningColumns}
                rowKey="id"
                pagination={false}
                size="small"
              />
            ) : (
              <Empty description="No running tasks" image={Empty.PRESENTED_IMAGE_SIMPLE} />
            )}
          </Card>

          {/* Failed Tasks */}
          {data.failed_tasks.length > 0 && (
            <Card
              title={<Space>Failed Tasks <Tag color="red">{data.failed_tasks_today}</Tag></Space>}
              style={{ marginBottom: 24 }}
            >
              <Table<CockpitTaskItem>
                dataSource={data.failed_tasks}
                columns={failedColumns}
                rowKey="id"
                pagination={false}
                size="small"
              />
            </Card>
          )}

          {/* Recent Completed */}
          <Card
            title="Recent Completed"
            style={{ marginBottom: 24 }}
          >
            {data.recent_completed.length > 0 ? (
              <Table<CockpitTaskItem>
                dataSource={data.recent_completed}
                columns={completedColumns}
                rowKey="id"
                pagination={false}
                size="small"
              />
            ) : (
              <Empty description="No completed tasks yet" image={Empty.PRESENTED_IMAGE_SIMPLE} />
            )}
          </Card>
        </>
      ) : (
        <Empty description="Unable to load cockpit data" />
      )}
    </div>
  );
};

export default CockpitPage;
