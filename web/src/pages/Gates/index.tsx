import React from 'react';
import { useLocation } from 'react-router-dom';
import { Row, Col, Typography, Spin, Empty, Segmented, message } from 'antd';
import { useGateList, useApproveGate, useRejectGate, useReviseGate } from '@/hooks/useGates';
import GateApprovalCard from '@/components/GateApprovalCard';

const { Title } = Typography;

const GatesPage: React.FC = () => {
  const location = useLocation();
  const searchParams = new URLSearchParams(location.search);
  const statusQuery = searchParams.get('status');
  const initialStatus = statusQuery === 'all' ? '' : (statusQuery || 'pending');

  const [statusFilter, setStatusFilter] = React.useState<string>(initialStatus);
  const { data: gates, isLoading } = useGateList({ status: statusFilter });
  const approveGate = useApproveGate();
  const rejectGate = useRejectGate();
  const reviseGate = useReviseGate();

  React.useEffect(() => {
    if (!isLoading && gates && location.hash) {
      // Small timeout to ensure DOM is updated
      setTimeout(() => {
        const id = location.hash.replace('#', '');
        const elem = document.getElementById(id);
        if (elem) {
          elem.scrollIntoView({ behavior: 'smooth', block: 'center' });
          elem.style.boxShadow = '0 0 0 2px #1890ff';
          setTimeout(() => {
            elem.style.boxShadow = '';
          }, 3000);
        }
      }, 100);
    }
  }, [isLoading, gates, location.hash]);

  const handleApprove = async (id: string, comment?: string) => {
    await approveGate.mutateAsync({ id, req: comment ? { comment } : undefined });
    message.success('Gate approved');
  };

  const handleReject = async (id: string, comment: string) => {
    await rejectGate.mutateAsync({ id, req: { comment } });
    message.success('Gate rejected');
  };

  const handleRevise = async (id: string, comment: string, revisedContent?: string) => {
    await reviseGate.mutateAsync({
      id,
      req: { comment, revised_content: revisedContent },
    });
    message.success('Revision submitted — stage will re-execute');
  };

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <Title level={4} style={{ margin: 0 }}>审批中心</Title>
        <Segmented
          value={statusFilter}
          onChange={(v) => setStatusFilter(v as string)}
          options={[
            { label: '待处理', value: 'pending' },
            { label: '已通过', value: 'approved' },
            { label: '已驳回', value: 'rejected' },
            { label: '已修订', value: 'revised' },
            { label: '全部', value: '' },
          ]}
        />
      </div>

      {isLoading ? (
        <Spin size="large" style={{ display: 'block', margin: '100px auto' }} />
      ) : !gates || gates.length === 0 ? (
        <Empty description="暂无审批记录" />
      ) : (
        <Row gutter={[16, 16]}>
          {gates.map((gate) => (
            <Col key={gate.id} xs={24} md={12} lg={8}>
              <GateApprovalCard
                gate={gate}
                onApprove={handleApprove}
                onReject={handleReject}
                onRevise={handleRevise}
                loading={approveGate.isPending || rejectGate.isPending || reviseGate.isPending}
              />
            </Col>
          ))}
        </Row>
      )}
    </div>
  );
};

export default GatesPage;
