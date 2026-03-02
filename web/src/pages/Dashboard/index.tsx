import React, { useEffect, useState } from 'react';
import { Row, Col, Card, Statistic, Typography, Badge, Button, Space, Modal, Form, InputNumber, Tag, Alert, Descriptions } from 'antd';
import { Link } from 'react-router-dom';
import { useAgentStore } from '@/stores/agentStore';
import { AGENT_ROLES } from '@/utils/constants';
import AgentCard from '@/components/AgentCard';
import ActivityFeed from '@/components/ActivityFeed';
import { useGateList } from '@/hooks/useGates';
import { useKPISummary } from '@/hooks/useKPI';
import { useAgentList } from '@/hooks/useAgents';
import { useLLMProbe } from '@/hooks/useLLMProbe';
import { formatTimestamp } from '@/utils/formatters';

const { Title } = Typography;

const Dashboard: React.FC = () => {
  const [probeOpen, setProbeOpen] = useState(false);
  const [probeForm] = Form.useForm<{ timeout_ms?: number }>();
  const agents = useAgentStore((s) => s.agents);
  const updateAgent = useAgentStore((s) => s.updateAgent);
  const { data: pendingGates } = useGateList({ status: 'pending' });
  const { data: kpiSummary } = useKPISummary();
  const { data: agentListData } = useAgentList();
  const llmProbe = useLLMProbe();
  const pendingCount = pendingGates?.length ?? 0;

  // Sync REST agent data into Zustand store on load
  useEffect(() => {
    if (agentListData?.agents) {
      for (const agent of agentListData.agents) {
        updateAgent(agent.role, {
          role: agent.role,
          status: agent.status as 'running' | 'idle' | 'waiting' | 'error' | 'stopped',
          model: agent.model_name || '未配置',
          current_task_id: agent.current_task_id || null,
        });
      }
    }
  }, [agentListData, updateAgent]);

  const handleProbe = async () => {
    try {
      const values = await probeForm.validateFields();
      await llmProbe.mutateAsync({
        timeout_ms: values.timeout_ms ?? 3000,
      });
    } catch (err: any) {
      if (err?.errorFields) return;
    }
  };

  return (
    <div>
      <Title level={4}>KPI 总览</Title>
      <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
        <Col xs={12} sm={6}>
          <Card size="small">
            <Statistic
              title="任务总数 / 已完成"
              value={kpiSummary?.completed_tasks ?? 0}
              suffix={`/ ${kpiSummary?.total_tasks ?? 0}`}
            />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card size="small">
            <Statistic
              title="Tokens 消耗"
              value={kpiSummary?.total_tokens ?? 0}
              suffix="tokens"
            />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card size="small">
            <Statistic
              title="成本"
              value={kpiSummary?.total_cost_rmb ?? 0}
              prefix="¥"
              precision={2}
            />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card size="small">
            <Link to="/gates">
              <Badge count={pendingCount} offset={[10, 0]}>
                <Statistic title="待处理审批" value={pendingCount} />
              </Badge>
            </Link>
          </Card>
        </Col>
      </Row>

      <Title level={4}>Agent 集群</Title>
      <Row gutter={[12, 12]} style={{ marginBottom: 24 }}>
        {AGENT_ROLES.map((role) => (
          <Col key={role.key} xs={12} sm={8} md={6} lg={4} xl={3}>
            <AgentCard agent={agents[role.key]} />
          </Col>
        ))}
      </Row>

      <Row gutter={16}>
        <Col xs={24} md={16}>
          <Card title="近期活动" size="small">
            <ActivityFeed />
          </Card>
        </Col>
        <Col xs={24} md={8}>
          <Card title="系统状态" size="small">
            <Statistic
              title="活跃 Agents"
              value={Object.values(agents).filter((a) => a.status === 'running' || a.status === 'idle').length}
              suffix={`/ ${AGENT_ROLES.length}`}
            />
            {kpiSummary && kpiSummary.success_rate > 0 && (
              <Statistic
                title="成功率"
                value={kpiSummary.success_rate}
                suffix="%"
                style={{ marginTop: 16 }}
              />
            )}
            <Button style={{ marginTop: 16 }} onClick={() => setProbeOpen(true)}>
              模型快速探活
            </Button>
          </Card>
        </Col>
      </Row>

      <Modal
        title="大模型快速探活"
        open={probeOpen}
        onCancel={() => setProbeOpen(false)}
        onOk={handleProbe}
        confirmLoading={llmProbe.isPending}
        okText="开始探活"
      >
        <Form form={probeForm} layout="vertical" initialValues={{ timeout_ms: 3000 }}>
          <Form.Item
            label="超时毫秒"
            name="timeout_ms"
            rules={[
              { required: true, message: '请输入 timeout_ms' },
              { type: 'number', min: 500, max: 10000, message: '范围应为 500..10000' },
            ]}
          >
            <InputNumber style={{ width: '100%' }} min={500} max={10000} step={100} />
          </Form.Item>
        </Form>

        {llmProbe.error && (
          <Alert
            type="error"
            showIcon
            style={{ marginTop: 12 }}
            message={(llmProbe.error as any)?.response?.data?.detail?.[0]?.msg || (llmProbe.error as any)?.response?.data?.detail || '探活请求失败，请重试'}
          />
        )}

        {llmProbe.data && (
          <div style={{ marginTop: 16 }}>
            <Space style={{ marginBottom: 12 }}>
              <Tag color={llmProbe.data.ok ? 'success' : 'error'}>
                {llmProbe.data.ok ? '可用' : '不可用'}
              </Tag>
              <Tag>延迟: {llmProbe.data.latency_ms}ms</Tag>
              {llmProbe.data.error_code && <Tag color="red">{llmProbe.data.error_code}</Tag>}
            </Space>
            <Descriptions size="small" column={1} bordered>
              <Descriptions.Item label="请求模型">{llmProbe.data.requested_model || '-'}</Descriptions.Item>
              <Descriptions.Item label="实际模型">{llmProbe.data.resolved_model || '-'}</Descriptions.Item>
              <Descriptions.Item label="Provider">{llmProbe.data.provider}</Descriptions.Item>
              <Descriptions.Item label="Base URL">{llmProbe.data.base_url}</Descriptions.Item>
              <Descriptions.Item label="Token 用量">
                {llmProbe.data.input_tokens} / {llmProbe.data.output_tokens} / {llmProbe.data.total_tokens}
              </Descriptions.Item>
              <Descriptions.Item label="检查时间">{formatTimestamp(llmProbe.data.checked_at)}</Descriptions.Item>
              {!llmProbe.data.ok && (
                <Descriptions.Item label="错误信息">{llmProbe.data.error_message || '-'}</Descriptions.Item>
              )}
            </Descriptions>
          </div>
        )}
      </Modal>
    </div>
  );
};

export default Dashboard;
