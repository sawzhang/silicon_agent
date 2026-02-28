import React, { useState } from 'react';
import { Card, Button, Space, Typography, Input, Tag } from 'antd';
import { CheckOutlined, CloseOutlined, EditOutlined } from '@ant-design/icons';
import type { Gate } from '@/types/gate';
import { formatRelativeTime } from '@/utils/formatters';

const { Text, Paragraph } = Typography;
const { TextArea } = Input;

interface GateApprovalCardProps {
  gate: Gate;
  onApprove: (id: string, comment?: string) => void;
  onReject: (id: string, comment: string) => void;
  onRevise?: (id: string, comment: string, revisedContent?: string) => void;
  loading?: boolean;
}

const GateApprovalCard: React.FC<GateApprovalCardProps> = ({ gate, onApprove, onReject, onRevise, loading }) => {
  const [comment, setComment] = useState('');
  const [revisedContent, setRevisedContent] = useState('');
  const [mode, setMode] = useState<'actions' | 'reject' | 'revise'>('actions');

  const waitingTime = formatRelativeTime(gate.created_at);
  const stageName = gate.content?.stage ?? gate.agent_role;
  const summary = gate.content?.summary ?? '';

  return (
    <Card
      id={`gate-card-${gate.id}`}
      size="small"
      title={
        <Space>
          <Tag color="orange">{gate.gate_type}</Tag>
          <Text>阶段：{stageName}</Text>
          {gate.is_dynamic && <Tag color="gold">动态</Tag>}
          {(gate.retry_count ?? 0) > 0 && <Tag color="blue">重试 {gate.retry_count}</Tag>}
        </Space>
      }
      extra={<Text type="secondary">{waitingTime}</Text>}
    >
      <Paragraph ellipsis={{ rows: 3, expandable: true }}>{summary}</Paragraph>
      <Paragraph type="secondary" style={{ fontSize: 12 }}>
        任务：{gate.task_id}
      </Paragraph>

      {gate.status === 'pending' ? (
        mode === 'reject' ? (
          <div>
            <Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 4 }}>
              你的反馈将发送给 AI 助手进行修改。
            </Paragraph>
            <TextArea
              rows={2}
              placeholder="请填写驳回原因..."
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              style={{ marginBottom: 8 }}
            />
            <Space>
              <Button
                danger
                size="small"
                loading={loading}
                onClick={() => onReject(gate.id, comment)}
                disabled={!comment.trim()}
              >
                确认驳回
              </Button>
              <Button size="small" onClick={() => setMode('actions')}>
                取消
              </Button>
            </Space>
          </div>
        ) : mode === 'revise' ? (
          <div>
            <Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 4 }}>
              提供修改意见。该阶段将根据你的指示重新执行。
            </Paragraph>
            <TextArea
              rows={2}
              placeholder="修改指示..."
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              style={{ marginBottom: 8 }}
            />
            <TextArea
              rows={3}
              placeholder="可选：在此处粘贴修改后的内容..."
              value={revisedContent}
              onChange={(e) => setRevisedContent(e.target.value)}
              style={{ marginBottom: 8 }}
            />
            <Space>
              <Button
                type="primary"
                size="small"
                loading={loading}
                onClick={() => onRevise?.(gate.id, comment, revisedContent || undefined)}
                disabled={!comment.trim()}
              >
                提交修改
              </Button>
              <Button size="small" onClick={() => setMode('actions')}>
                取消
              </Button>
            </Space>
          </div>
        ) : (
          <Space>
            <Button
              type="primary"
              icon={<CheckOutlined />}
              size="small"
              loading={loading}
              onClick={() => onApprove(gate.id, comment || undefined)}
            >
              通过
            </Button>
            {onRevise && (
              <Button
                icon={<EditOutlined />}
                size="small"
                onClick={() => setMode('revise')}
              >
                修订
              </Button>
            )}
            <Button
              danger
              icon={<CloseOutlined />}
              size="small"
              onClick={() => setMode('reject')}
            >
              驳回
            </Button>
          </Space>
        )
      ) : (
        <Space>
          <Tag color={gate.status === 'approved' ? 'green' : gate.status === 'revised' ? 'blue' : 'red'}>
            {gate.status}
          </Tag>
          {gate.reviewer && <Text type="secondary">by {gate.reviewer}</Text>}
        </Space>
      )}
    </Card>
  );
};

export default GateApprovalCard;
