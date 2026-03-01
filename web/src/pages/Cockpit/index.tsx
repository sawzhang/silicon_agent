import React, { useState } from 'react';
import { Row, Col, Typography, Spin, Card, Empty, Statistic, Table, Tag, Button, Space, Popconfirm, message } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useNavigate } from 'react-router-dom';
import { useCockpit } from '@/hooks/useKPI';
import { useCancelTask, useRetryTask } from '@/hooks/useTasks';
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
  const cancelTask = useCancelTask();
  const retryTask = useRetryTask();
  const [gateLoading, setGateLoading] = useState<string | null>(null);
  const [cancellingId, setCancellingId] = useState<string | null>(null);
  const [retryingId, setRetryingId] = useState<string | null>(null);

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

  const handleCancel = async (id: string) => {
    setCancellingId(id);
    try {
      await cancelTask.mutateAsync(id);
      message.success('任务已取消');
    } finally {
      setCancellingId(null);
    }
  };

  const handleRetry = async (id: string) => {
    setRetryingId(id);
    try {
      await retryTask.mutateAsync(id);
      message.success('任务已重新提交');
    } finally {
      setRetryingId(null);
    }
  };

  const runningColumns: ColumnsType<CockpitTaskItem> = [
    {
      title: '标题',
      dataIndex: 'title',
      key: 'title',
      ellipsis: true,
      render: (text: string, record) => (
        <a onClick={() => navigate(`/tasks/${record.id}`)}>{text}</a>
      ),
    },
    { title: '项目', dataIndex: 'project_name', key: 'project_name', width: 120, render: (v: string | null) => v ?? '-' },
    {
      title: '当前阶段',
      dataIndex: 'current_stage',
      key: 'current_stage',
      width: 130,
      render: (v: string | null) => v ? <Tag color="processing">{v}</Tag> : '-',
    },
    {
      title: 'Tokens 消耗',
      dataIndex: 'total_tokens',
      key: 'total_tokens',
      width: 100,
      render: (v: number) => formatTokens(v),
    },
    {
      title: '操作',
      key: 'action',
      width: 80,
      render: (_: unknown, record) => (
        <Popconfirm title="确认取消此任务？" onConfirm={() => handleCancel(record.id)}>
          <Button size="small" danger loading={cancellingId === record.id}>取消</Button>
        </Popconfirm>
      ),
    },
  ];

  const failedColumns: ColumnsType<CockpitTaskItem> = [
    {
      title: '标题',
      dataIndex: 'title',
      key: 'title',
      ellipsis: true,
      render: (text: string, record) => (
        <a onClick={() => navigate(`/tasks/${record.id}`)}>{text}</a>
      ),
    },
    {
      title: '错误信息',
      dataIndex: 'error_message',
      key: 'error_message',
      ellipsis: true,
      render: (v: string | null) => v ? <Text type="danger">{v}</Text> : '-',
    },
    {
      title: '成本',
      dataIndex: 'total_cost_rmb',
      key: 'total_cost_rmb',
      width: 90,
      render: (v: number) => formatCost(v),
    },
    {
      title: '操作',
      key: 'action',
      width: 140,
      render: (_: unknown, record) => (
        <Space>
          <Popconfirm title="确认重试此任务？" onConfirm={() => handleRetry(record.id)}>
            <Button size="small" type="primary" loading={retryingId === record.id}>重试</Button>
          </Popconfirm>
          <Button size="small" onClick={() => navigate(`/tasks/${record.id}`)}>查看</Button>
        </Space>
      ),
    },
  ];

  const completedColumns: ColumnsType<CockpitTaskItem> = [
    {
      title: '标题',
      dataIndex: 'title',
      key: 'title',
      ellipsis: true,
      render: (text: string, record) => (
        <a onClick={() => navigate(`/tasks/${record.id}`)}>{text}</a>
      ),
    },
    {
      title: '完成时间',
      dataIndex: 'completed_at',
      key: 'completed_at',
      width: 120,
      render: (v: string | null) => v ? formatRelativeTime(v) : '-',
    },
    {
      title: 'Tokens 消耗',
      dataIndex: 'total_tokens',
      key: 'total_tokens',
      width: 100,
      render: (v: number) => formatTokens(v),
    },
    {
      title: '成本',
      dataIndex: 'total_cost_rmb',
      key: 'total_cost_rmb',
      width: 90,
      render: (v: number) => formatCost(v),
    },
  ];

  return (
    <div>
      <Title level={4} style={{ marginBottom: 16 }}>指挥台</Title>

      {isLoading ? (
        <Spin size="large" style={{ display: 'block', margin: '40px auto' }} />
      ) : data ? (
        <>
          <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
            <Col xs={12} sm={6}>
              <Card size="small">
                <Statistic
                  title="待处理审批"
                  value={data.pending_gates_count}
                  valueStyle={data.pending_gates_count > 0 ? { color: '#fa8c16' } : undefined}
                />
              </Card>
            </Col>
            <Col xs={12} sm={6}>
              <Card size="small">
                <Statistic
                  title="运行中任务"
                  value={data.running_tasks_count}
                  valueStyle={{ color: '#1890ff' }}
                />
              </Card>
            </Col>
            <Col xs={12} sm={6}>
              <Card size="small">
                <Statistic
                  title="今日失败任务"
                  value={data.failed_tasks_today}
                  valueStyle={data.failed_tasks_today > 0 ? { color: '#cf1322' } : undefined}
                />
              </Card>
            </Col>
            <Col xs={12} sm={6}>
              <Card size="small">
                <Statistic
                  title="今日已完成"
                  value={data.completed_tasks_today}
                  valueStyle={{ color: '#3f8600' }}
                />
              </Card>
            </Col>
          </Row>

          {/* Pending Gates */}
          <Card
            title={<Space>待处理审批 <Tag color="orange">{data.pending_gates_count}</Tag></Space>}
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
              <Empty description="暂无待处理审批" image={Empty.PRESENTED_IMAGE_SIMPLE} />
            )}
          </Card>

          {/* Running Tasks */}
          <Card
            title={<Space>运行中任务 <Tag color="blue">{data.running_tasks_count}</Tag></Space>}
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
              <Empty description="暂无运行中任务" image={Empty.PRESENTED_IMAGE_SIMPLE} />
            )}
          </Card>

          {/* Failed Tasks */}
          {data.failed_tasks.length > 0 && (
            <Card
              title={<Space>失败任务 <Tag color="red">{data.failed_tasks_today}</Tag></Space>}
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
            title="近期完成的任务"
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
              <Empty description="暂无已完成的任务" image={Empty.PRESENTED_IMAGE_SIMPLE} />
            )}
          </Card>
        </>
      ) : (
        <Empty description="无法加载指挥台数据" />
      )}
    </div>
  );
};

export default CockpitPage;
