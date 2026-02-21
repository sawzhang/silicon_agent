import React, { useState } from 'react';
import { Card, Button, Space, Typography, Input, Tag } from 'antd';
import { CheckOutlined, CloseOutlined } from '@ant-design/icons';
import type { Gate } from '@/types/gate';
import { formatRelativeTime } from '@/utils/formatters';

const { Text, Paragraph } = Typography;
const { TextArea } = Input;

interface GateApprovalCardProps {
  gate: Gate;
  onApprove: (id: string, comment?: string) => void;
  onReject: (id: string, comment: string, reason: string) => void;
  loading?: boolean;
}

const GateApprovalCard: React.FC<GateApprovalCardProps> = ({ gate, onApprove, onReject, loading }) => {
  const [comment, setComment] = useState('');
  const [showReject, setShowReject] = useState(false);

  const waitingTime = formatRelativeTime(gate.requested_at);

  return (
    <Card
      size="small"
      title={
        <Space>
          <Tag color="orange">{gate.gate_type}</Tag>
          <Text>Stage: {gate.stage}</Text>
        </Space>
      }
      extra={<Text type="secondary">{waitingTime}</Text>}
    >
      <Paragraph ellipsis={{ rows: 3, expandable: true }}>{gate.summary}</Paragraph>
      <Paragraph type="secondary" style={{ fontSize: 12 }}>
        Task: {gate.task_id}
      </Paragraph>

      {showReject ? (
        <div>
          <TextArea
            rows={2}
            placeholder="Rejection reason..."
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            style={{ marginBottom: 8 }}
          />
          <Space>
            <Button
              danger
              size="small"
              loading={loading}
              onClick={() => onReject(gate.id, comment, comment)}
              disabled={!comment.trim()}
            >
              Confirm Reject
            </Button>
            <Button size="small" onClick={() => setShowReject(false)}>
              Cancel
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
            Approve
          </Button>
          <Button
            danger
            icon={<CloseOutlined />}
            size="small"
            onClick={() => setShowReject(true)}
          >
            Reject
          </Button>
        </Space>
      )}
    </Card>
  );
};

export default GateApprovalCard;
