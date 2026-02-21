import React from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Card, Descriptions, Tag, Button, Spin, Typography, Space, message } from 'antd';
import { ArrowLeftOutlined, StopOutlined } from '@ant-design/icons';
import { useTask, useCancelTask } from '@/hooks/useTasks';
import PipelineView from '@/components/PipelineView';
import { formatTimestamp, formatTokens, formatCost, formatDuration } from '@/utils/formatters';

const { Title } = Typography;

const STATUS_COLOR: Record<string, string> = {
  queued: 'default',
  running: 'processing',
  completed: 'success',
  failed: 'error',
  cancelled: 'warning',
};

const TaskDetail: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { data: task, isLoading } = useTask(id!);
  const cancelTask = useCancelTask();

  if (isLoading || !task) {
    return <Spin size="large" style={{ display: 'block', margin: '100px auto' }} />;
  }

  const duration = task.started_at && task.completed_at
    ? (new Date(task.completed_at).getTime() - new Date(task.started_at).getTime()) / 1000
    : null;

  return (
    <div>
      <Space style={{ marginBottom: 16 }}>
        <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/tasks')}>
          Back
        </Button>
        {task.status === 'running' && (
          <Button
            danger
            icon={<StopOutlined />}
            onClick={async () => {
              await cancelTask.mutateAsync(task.id);
              message.success('Task cancelled');
            }}
            loading={cancelTask.isPending}
          >
            Cancel Task
          </Button>
        )}
      </Space>

      <Title level={4}>{task.title}</Title>

      <Card style={{ marginBottom: 16 }}>
        <PipelineView stages={task.stages} />
      </Card>

      <Card title="Task Details" style={{ marginBottom: 16 }}>
        <Descriptions column={2}>
          <Descriptions.Item label="ID">{task.id}</Descriptions.Item>
          <Descriptions.Item label="Status">
            <Tag color={STATUS_COLOR[task.status]}>{task.status}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="Priority">{task.priority}</Descriptions.Item>
          <Descriptions.Item label="Created By">{task.created_by}</Descriptions.Item>
          <Descriptions.Item label="Created At">{formatTimestamp(task.created_at)}</Descriptions.Item>
          <Descriptions.Item label="Started At">{task.started_at ? formatTimestamp(task.started_at) : '-'}</Descriptions.Item>
          <Descriptions.Item label="Completed At">{task.completed_at ? formatTimestamp(task.completed_at) : '-'}</Descriptions.Item>
          <Descriptions.Item label="Duration">{duration != null ? formatDuration(duration) : '-'}</Descriptions.Item>
          <Descriptions.Item label="Total Tokens">{formatTokens(task.total_tokens)}</Descriptions.Item>
          <Descriptions.Item label="Total Cost">{formatCost(task.total_cost_usd)}</Descriptions.Item>
        </Descriptions>
      </Card>

      <Card title="Description">
        <Typography.Paragraph>{task.description}</Typography.Paragraph>
      </Card>
    </div>
  );
};

export default TaskDetail;
