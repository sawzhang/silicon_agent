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
      title: '停止所有 Agent',
      icon: <ExclamationCircleOutlined />,
      content: '确定要停止所有 Agent 吗？这将中断所有正在运行的任务。',
      okText: '全部停止',
      okType: 'danger',
      onOk: async () => {
        setStopAllLoading(true);
        try {
          await Promise.all(
            Object.keys(agents)
              .filter((role) => agents[role].status !== 'stopped')
              .map((role) => stopAgent(role)),
          );
          message.success('所有 Agent 已停止');
        } catch {
          message.error('部分 Agent 停止失败');
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
      message.success('所有 Agent 已启动');
    } catch {
      message.error('部分 Agent 启动失败');
    }
  };

  return (
    <div>
      <Title level={4}>止损控制台</Title>

      {errorCount > 0 && (
        <Alert
          type="error"
          message={`检测到 ${errorCount} 个 Agent 处于异常状态`}
          description="请检查 Agent 运行日志，在解决报错后重新启动。"
          showIcon
          style={{ marginBottom: 16 }}
        />
      )}

      <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
        <Col xs={12} sm={8}>
          <Card size="small">
            <Statistic title="活跃 Agent" value={activeCount} suffix={`/ ${AGENT_ROLES.length}`} />
          </Card>
        </Col>
        <Col xs={12} sm={8}>
          <Card size="small">
            <Statistic title="异常 Agent" value={errorCount} valueStyle={errorCount > 0 ? { color: '#cf1322' } : undefined} />
          </Card>
        </Col>
        <Col xs={24} sm={8}>
          <Card size="small">
            <Space>
              <Button danger type="primary" icon={<StopOutlined />} onClick={handleStopAll} loading={stopAllLoading}>
                全部停止
              </Button>
              <Button type="primary" icon={<CheckCircleOutlined />} onClick={handleStartAll}>
                全部启动
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
                    <Button size="small" danger onClick={() => stopAgent(role.key).then(() => message.success(`${role.name} 已停止`))}>
                      停止
                    </Button>
                  ) : (
                    <Button size="small" type="primary" onClick={() => startAgent(role.key).then(() => message.success(`${role.name} 已启动`))}>
                      启动
                    </Button>
                  )
                }
              >
                {agent.error_message && (
                  <Alert type="error" message={agent.error_message} style={{ marginBottom: 8 }} />
                )}
                <Text type="secondary" style={{ fontSize: 12 }}>
                  模型：{agent.model}
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
