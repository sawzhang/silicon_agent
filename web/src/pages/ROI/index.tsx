import React, { useState } from 'react';
import { Row, Col, Typography, Spin, Card, Empty, Statistic, Table, Radio } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import ReactECharts from 'echarts-for-react';
import { useROISummary } from '@/hooks/useKPI';
import { formatTokens, formatCost } from '@/utils/formatters';
import type { AgentRoleEfficiency, ROITaskBreakdown } from '@/types/kpi';

const { Title, Text } = Typography;

const DAYS_OPTIONS = [
  { label: '7d', value: 7 },
  { label: '30d', value: 30 },
  { label: '90d', value: 90 },
  { label: 'All', value: 365 },
];

const roleColumns: ColumnsType<AgentRoleEfficiency> = [
  { title: 'Role', dataIndex: 'display_name', key: 'display_name' },
  { title: 'Stages', dataIndex: 'total_stages', key: 'total_stages' },
  {
    title: 'Tokens',
    dataIndex: 'total_tokens',
    key: 'total_tokens',
    render: (v: number) => formatTokens(v),
  },
  {
    title: 'Avg Duration',
    dataIndex: 'avg_duration_seconds',
    key: 'avg_duration_seconds',
    render: (v: number) => `${(v / 60).toFixed(1)} min`,
  },
  {
    title: 'Cost',
    dataIndex: 'total_cost_rmb',
    key: 'total_cost_rmb',
    render: (v: number) => formatCost(v),
  },
];

const taskColumns: ColumnsType<ROITaskBreakdown> = [
  { title: 'Title', dataIndex: 'title', key: 'title', ellipsis: true },
  {
    title: 'Agent Cost',
    dataIndex: 'agent_cost_rmb',
    key: 'agent_cost_rmb',
    render: (v: number) => formatCost(v),
    width: 110,
  },
  {
    title: 'Manual Est.',
    dataIndex: 'estimated_manual_rmb',
    key: 'estimated_manual_rmb',
    render: (v: number) => formatCost(v),
    width: 110,
  },
  {
    title: 'Savings',
    dataIndex: 'savings_rmb',
    key: 'savings_rmb',
    render: (v: number) => (
      <Text type={v > 0 ? 'success' : 'danger'}>{formatCost(v)}</Text>
    ),
    width: 110,
  },
  {
    title: 'Agent Time',
    dataIndex: 'agent_duration_minutes',
    key: 'agent_duration_minutes',
    render: (v: number) => `${v.toFixed(1)} min`,
    width: 110,
  },
];

const ROIPage: React.FC = () => {
  const [days, setDays] = useState(30);
  const { data, isLoading } = useROISummary(days);

  const chartOption = data
    ? {
        tooltip: { trigger: 'axis' as const },
        legend: { data: ['Agent Cost', 'Manual Estimate'] },
        xAxis: { type: 'category' as const, data: ['Cost Comparison'] },
        yAxis: { type: 'value' as const, name: '¥ RMB' },
        series: [
          {
            name: 'Agent Cost',
            type: 'bar',
            data: [data.total_agent_cost_rmb],
            itemStyle: { color: '#1890ff' },
            barWidth: 60,
          },
          {
            name: 'Manual Estimate',
            type: 'bar',
            data: [data.total_estimated_manual_rmb],
            itemStyle: { color: '#ff7a45' },
            barWidth: 60,
          },
        ],
      }
    : {};

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <Title level={4} style={{ margin: 0 }}>ROI Dashboard</Title>
        <Radio.Group
          optionType="button"
          buttonStyle="solid"
          value={days}
          onChange={(e) => setDays(e.target.value)}
          options={DAYS_OPTIONS}
        />
      </div>

      {isLoading ? (
        <Spin size="large" style={{ display: 'block', margin: '40px auto' }} />
      ) : data ? (
        <>
          <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
            <Col xs={12} sm={6}>
              <Card size="small">
                <Statistic
                  title="Total Savings"
                  value={data.total_savings_rmb}
                  prefix="¥"
                  precision={2}
                  valueStyle={{ color: '#3f8600' }}
                />
              </Card>
            </Col>
            <Col xs={12} sm={6}>
              <Card size="small">
                <Statistic
                  title="ROI Ratio"
                  value={data.roi_ratio}
                  suffix="x"
                  precision={1}
                  valueStyle={{ color: '#1890ff' }}
                />
              </Card>
            </Col>
            <Col xs={12} sm={6}>
              <Card size="small">
                <Statistic
                  title="Time Saved"
                  value={data.time_saved_hours}
                  suffix="h"
                  precision={1}
                />
              </Card>
            </Col>
            <Col xs={12} sm={6}>
              <Card size="small">
                <Statistic title="Tasks Automated" value={data.total_tasks_completed} />
              </Card>
            </Col>
          </Row>

          <Card title="Cost Comparison" style={{ marginBottom: 24 }}>
            {data.total_tasks_completed > 0 ? (
              <ReactECharts option={chartOption} style={{ height: 300 }} />
            ) : (
              <Empty description="No completed tasks in this period" />
            )}
          </Card>

          <Card title="Agent Role Efficiency" style={{ marginBottom: 24 }}>
            <Table<AgentRoleEfficiency>
              dataSource={data.by_role}
              columns={roleColumns}
              rowKey="role"
              pagination={false}
              size="small"
            />
          </Card>

          <Card title="Recent Tasks ROI" style={{ marginBottom: 24 }}>
            <Table<ROITaskBreakdown>
              dataSource={data.recent_tasks}
              columns={taskColumns}
              rowKey="task_id"
              pagination={false}
              size="small"
            />
          </Card>

          <Text type="secondary">
            Benchmarks: {data.benchmark_hours_per_task}h/task @ ¥{data.benchmark_hourly_rate}/h (configurable via .env)
          </Text>
        </>
      ) : (
        <Empty description="No ROI data available" />
      )}
    </div>
  );
};

export default ROIPage;
