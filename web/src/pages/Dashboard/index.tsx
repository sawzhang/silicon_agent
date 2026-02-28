import React, { useEffect } from 'react';
import { Row, Col, Card, Statistic, Typography, Badge } from 'antd';
import { Link } from 'react-router-dom';
import { useAgentStore } from '@/stores/agentStore';
import { AGENT_ROLES } from '@/utils/constants';
import AgentCard from '@/components/AgentCard';
import ActivityFeed from '@/components/ActivityFeed';
import { useGateList } from '@/hooks/useGates';
import { useKPISummary } from '@/hooks/useKPI';
import { useAgentList } from '@/hooks/useAgents';

const { Title } = Typography;

const Dashboard: React.FC = () => {
  const agents = useAgentStore((s) => s.agents);
  const updateAgent = useAgentStore((s) => s.updateAgent);
  const { data: pendingGates } = useGateList({ status: 'pending' });
  const { data: kpiSummary } = useKPISummary();
  const { data: agentListData } = useAgentList();
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
          </Card>
        </Col>
      </Row>
    </div>
  );
};

export default Dashboard;
