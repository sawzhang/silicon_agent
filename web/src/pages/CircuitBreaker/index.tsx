import React, { useState } from 'react';
import { Card, Row, Col, Typography, Tag, Button, Space, Statistic, Alert, Modal, Input, message } from 'antd';
import { ExclamationCircleOutlined, CheckCircleOutlined, StopOutlined } from '@ant-design/icons';
import { useAgentStore } from '@/stores/agentStore';
import { stopAgent, startAgent } from '@/services/agentApi';
import { AGENT_ROLES, ROLE_DISPLAY_NAMES } from '@/utils/constants';

const { Title, Text } = Typography;

const CircuitBreakerPage: React.FC = () => {
  const agents = useAgentStore((s) => s.agents);
  const [stopAllLoading, setStopAllLoading] = useState(false);

  const activeCount = Object.values(agents).filter((a) => a.status !== 'stopped').length;
  const errorCount = Object.values(agents).filter((a) => a.status === 'error').length;

  const handleStopAll = () => {
    Modal.confirm({
      title: 'Stop All Agents',
      icon: <ExclamationCircleOutlined />,
      content: 'Are you sure you want to stop all agents? This will halt all running tasks.',
      okText: 'Stop All',
      okType: 'danger',
      onOk: async () => {
        setStopAllLoading(true);
        try {
          await Promise.all(
            Object.keys(agents)
              .filter((role) => agents[role].status !== 'stopped')
              .map((role) => stopAgent(role)),
          );
          message.success('All agents stopped');
        } catch {
          message.error('Failed to stop some agents');
        } finally {
          setStopAllLoading(false);
        }
      },
    });
  };

  const handleStartAll = async () => {
    try {
      await Promise.all(
        Object.keys(agents)
          .filter((role) => agents[role].status === 'stopped')
          .map((role) => startAgent(role)),
      );
      message.success('All agents started');
    } catch {
      message.error('Failed to start some agents');
    }
  };

  return (
    <div>
      <Title level={4}>Circuit Breaker Control</Title>

      {errorCount > 0 && (
        <Alert
          type="error"
          message={`${errorCount} agent(s) in error state`}
          description="Check agent logs for details and resolve errors before restarting."
          showIcon
          style={{ marginBottom: 16 }}
        />
      )}

      <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
        <Col xs={12} sm={8}>
          <Card size="small">
            <Statistic title="Active Agents" value={activeCount} suffix={`/ ${AGENT_ROLES.length}`} />
          </Card>
        </Col>
        <Col xs={12} sm={8}>
          <Card size="small">
            <Statistic title="Error Agents" value={errorCount} valueStyle={errorCount > 0 ? { color: '#cf1322' } : undefined} />
          </Card>
        </Col>
        <Col xs={24} sm={8}>
          <Card size="small">
            <Space>
              <Button danger type="primary" icon={<StopOutlined />} onClick={handleStopAll} loading={stopAllLoading}>
                Stop All
              </Button>
              <Button type="primary" icon={<CheckCircleOutlined />} onClick={handleStartAll}>
                Start All
              </Button>
            </Space>
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]}>
        {AGENT_ROLES.map((role) => {
          const agent = agents[role.key];
          if (!agent) return null;
          const isActive = agent.status !== 'stopped';
          return (
            <Col key={role.key} xs={24} sm={12} md={8}>
              <Card
                size="small"
                title={
                  <Space>
                    <Text strong>{role.name}</Text>
                    <Tag color={isActive ? 'green' : 'default'}>{agent.status}</Tag>
                  </Space>
                }
                extra={
                  isActive ? (
                    <Button size="small" danger onClick={() => stopAgent(role.key).then(() => message.success(`${role.name} stopped`))}>
                      Stop
                    </Button>
                  ) : (
                    <Button size="small" type="primary" onClick={() => startAgent(role.key).then(() => message.success(`${role.name} started`))}>
                      Start
                    </Button>
                  )
                }
              >
                {agent.error_message && (
                  <Alert type="error" message={agent.error_message} style={{ marginBottom: 8 }} />
                )}
                <Text type="secondary" style={{ fontSize: 12 }}>
                  Model: {agent.model}
                </Text>
              </Card>
            </Col>
          );
        })}
      </Row>
    </div>
  );
};

export default CircuitBreakerPage;
