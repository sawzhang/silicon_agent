import React from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Card, Collapse, Descriptions, Empty, Tag, Button, Spin, Typography, Space, message } from 'antd';
import { ArrowLeftOutlined, StopOutlined, ReloadOutlined } from '@ant-design/icons';
import { useTask, useCancelTask, useRetryTask } from '@/hooks/useTasks';
import PipelineView from '@/components/PipelineView';
import { STAGE_NAMES } from '@/utils/constants';
import { formatTimestamp, formatTokens, formatCost, formatDuration } from '@/utils/formatters';

const { Title } = Typography;

const STATUS_COLOR: Record<string, string> = {
  pending: 'default',
  running: 'processing',
  completed: 'success',
  failed: 'error',
  cancelled: 'warning',
  skipped: 'default',
};

const STAGE_DISPLAY: Record<string, string> = Object.fromEntries(
  STAGE_NAMES.map((sn) => [sn.key, sn.name])
);

const TaskDetail: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { data: task, isLoading } = useTask(id!);
  const cancelTask = useCancelTask();
  const retryTask = useRetryTask();

  if (isLoading || !task) {
    return <Spin size="large" style={{ display: 'block', margin: '100px auto' }} />;
  }

  const duration = task.created_at && task.completed_at
    ? (new Date(task.completed_at).getTime() - new Date(task.created_at).getTime()) / 1000
    : null;

  return (
    <div>
      <Space style={{ marginBottom: 16 }}>
        <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/tasks')}>
          Back
        </Button>
        {(task.status === 'running' || task.status === 'pending') && (
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
        {task.status === 'failed' && (
          <Button
            type="primary"
            icon={<ReloadOutlined />}
            onClick={async () => {
              await retryTask.mutateAsync(task.id);
              message.success('任务已重新提交，将从失败阶段继续执行');
            }}
            loading={retryTask.isPending}
          >
            重试任务
          </Button>
        )}
      </Space>

      <Title level={4}>{task.title}</Title>

      <Card style={{ marginBottom: 16 }}>
        <PipelineView stages={task.stages} />
      </Card>

      {task.stages.length > 0 && (
        <Card title="阶段产出" style={{ marginBottom: 16 }}>
          <Collapse accordion>
            {task.stages.map((stage) => (
              <Collapse.Panel
                key={stage.id}
                header={
                  <Space>
                    <Tag color={STATUS_COLOR[stage.status]}>{stage.status}</Tag>
                    <span>{STAGE_DISPLAY[stage.stage_name] || stage.stage_name}</span>
                    <span style={{ color: '#999' }}>
                      {stage.tokens_used > 0 && `${stage.tokens_used.toLocaleString()} tokens`}
                      {stage.duration_seconds != null && ` · ${stage.duration_seconds.toFixed(1)}s`}
                    </span>
                  </Space>
                }
              >
                {stage.output_summary ? (
                  <Typography.Paragraph style={{ whiteSpace: 'pre-wrap' }}>
                    {stage.output_summary}
                  </Typography.Paragraph>
                ) : stage.error_message ? (
                  <div>
                    <Typography.Text type="danger">{stage.error_message}</Typography.Text>
                    {task.status === 'failed' && (
                      <div style={{ marginTop: 12 }}>
                        <Button
                          type="primary"
                          size="small"
                          icon={<ReloadOutlined />}
                          onClick={async () => {
                            await retryTask.mutateAsync(task.id);
                            message.success('任务已重新提交，将从失败阶段继续执行');
                          }}
                          loading={retryTask.isPending}
                        >
                          从此阶段重试
                        </Button>
                      </div>
                    )}
                  </div>
                ) : (
                  <Empty description="暂无产出" />
                )}
              </Collapse.Panel>
            ))}
          </Collapse>
        </Card>
      )}

      <Card title="Task Details" style={{ marginBottom: 16 }}>
        <Descriptions column={2}>
          <Descriptions.Item label="ID">{task.id}</Descriptions.Item>
          <Descriptions.Item label="Status">
            <Tag color={STATUS_COLOR[task.status]}>{task.status}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="Template">{task.template_name || '-'}</Descriptions.Item>
          <Descriptions.Item label="Project">{task.project_name || '-'}</Descriptions.Item>
          <Descriptions.Item label="Created At">{formatTimestamp(task.created_at)}</Descriptions.Item>
          <Descriptions.Item label="Completed At">{task.completed_at ? formatTimestamp(task.completed_at) : '-'}</Descriptions.Item>
          <Descriptions.Item label="Duration">{duration != null ? formatDuration(duration) : '-'}</Descriptions.Item>
          <Descriptions.Item label="Total Tokens">{formatTokens(task.total_tokens)}</Descriptions.Item>
          <Descriptions.Item label="Total Cost">{formatCost(task.total_cost_rmb)}</Descriptions.Item>
        </Descriptions>
      </Card>

      <Card title="Description">
        <Typography.Paragraph>{task.description}</Typography.Paragraph>
      </Card>
    </div>
  );
};

export default TaskDetail;
