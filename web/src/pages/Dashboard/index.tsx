import React from 'react';
import { Row, Col, Card, Statistic, Typography, Badge } from 'antd';
import { Link } from 'react-router-dom';
import { useAgentStore } from '@/stores/agentStore';
import { AGENT_ROLES } from '@/utils/constants';
import AgentCard from '@/components/AgentCard';
import ActivityFeed from '@/components/ActivityFeed';
import { useGateList } from '@/hooks/useGates';

const { Title } = Typography;

const Dashboard: React.FC = () => {
  const agents = useAgentStore((s) => s.agents);
  const { data: pendingGates } = useGateList({ status: 'pending' });
  const pendingCount = pendingGates?.length ?? 0;

  return (
    <div>
      <Title level={4}>KPI Overview</Title>
      <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
        <Col xs={12} sm={6}>
          <Card size="small">
            <Statistic title="Tasks Completed" value={0} suffix="/ day" />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card size="small">
            <Statistic title="Tokens Used" value={0} suffix="K" />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card size="small">
            <Statistic title="Cost" value={0} prefix="¥" precision={2} />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card size="small">
            <Link to="/gates">
              <Badge count={pendingCount} offset={[10, 0]}>
                <Statistic title="Pending Gates" value={pendingCount} />
              </Badge>
            </Link>
          </Card>
        </Col>
      </Row>

      <Title level={4}>Agent Cluster</Title>
      <Row gutter={[12, 12]} style={{ marginBottom: 24 }}>
        {AGENT_ROLES.map((role) => (
          <Col key={role.key} xs={12} sm={8} md={6} lg={4} xl={3}>
            <AgentCard agent={agents[role.key]} />
          </Col>
        ))}
      </Row>

      <Row gutter={16}>
        <Col xs={24} md={16}>
          <Card title="Recent Activity" size="small">
            <ActivityFeed />
          </Card>
        </Col>
        <Col xs={24} md={8}>
          <Card title="System Status" size="small">
            <Statistic
              title="Active Agents"
              value={Object.values(agents).filter((a) => a.status === 'running' || a.status === 'idle').length}
              suffix={`/ ${AGENT_ROLES.length}`}
            />
          </Card>
        </Col>
      </Row>
    </div>
  );
};

export default Dashboard;
