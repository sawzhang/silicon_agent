import React from 'react';
import { Row, Col, Typography, Spin, Empty, Segmented, message } from 'antd';
import { useGateList, useApproveGate, useRejectGate } from '@/hooks/useGates';
import GateApprovalCard from '@/components/GateApprovalCard';

const { Title } = Typography;

const GatesPage: React.FC = () => {
  const [statusFilter, setStatusFilter] = React.useState<string>('pending');
  const { data: gates, isLoading } = useGateList({ status: statusFilter });
  const approveGate = useApproveGate();
  const rejectGate = useRejectGate();

  const handleApprove = async (id: string, comment?: string) => {
    await approveGate.mutateAsync({ id, req: comment ? { comment } : undefined });
    message.success('Gate approved');
  };

  const handleReject = async (id: string, comment: string, reason: string) => {
    await rejectGate.mutateAsync({ id, req: { comment, reason } });
    message.success('Gate rejected');
  };

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <Title level={4} style={{ margin: 0 }}>Approval Center</Title>
        <Segmented
          value={statusFilter}
          onChange={(v) => setStatusFilter(v as string)}
          options={[
            { label: 'Pending', value: 'pending' },
            { label: 'Approved', value: 'approved' },
            { label: 'Rejected', value: 'rejected' },
            { label: 'All', value: '' },
          ]}
        />
      </div>

      {isLoading ? (
        <Spin size="large" style={{ display: 'block', margin: '100px auto' }} />
      ) : !gates || gates.length === 0 ? (
        <Empty description="No gates found" />
      ) : (
        <Row gutter={[16, 16]}>
          {gates.map((gate) => (
            <Col key={gate.id} xs={24} md={12} lg={8}>
              <GateApprovalCard
                gate={gate}
                onApprove={handleApprove}
                onReject={handleReject}
                loading={approveGate.isPending || rejectGate.isPending}
              />
            </Col>
          ))}
        </Row>
      )}
    </div>
  );
};

export default GatesPage;
