import React, { useMemo, useState } from 'react';
import { Row, Col, Typography, Spin, Card, Empty, Statistic, Table, Radio, Slider, Tag } from 'antd';
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
  { title: '角色', dataIndex: 'display_name', key: 'display_name' },
  { title: '执行阶段数', dataIndex: 'total_stages', key: 'total_stages' },
  {
    title: '消耗 Tokens',
    dataIndex: 'total_tokens',
    key: 'total_tokens',
    render: (v: number) => formatTokens(v),
  },
  {
    title: '平均耗时',
    dataIndex: 'avg_duration_seconds',
    key: 'avg_duration_seconds',
    render: (v: number) => `${(v / 60).toFixed(1)} 分钟`,
  },
  {
    title: '总成本',
    dataIndex: 'total_cost_rmb',
    key: 'total_cost_rmb',
    render: (v: number) => formatCost(v),
  },
];

const ROIPage: React.FC = () => {
  const [days, setDays] = useState(30);
  const { data, isLoading } = useROISummary(days);

  // What-if slider state: null means use API defaults
  const [whatIfHours, setWhatIfHours] = useState<number | null>(null);
  const [whatIfRate, setWhatIfRate] = useState<number | null>(null);

  const effectiveHours = whatIfHours ?? data?.benchmark_hours_per_task ?? 8;
  const effectiveRate = whatIfRate ?? data?.benchmark_hourly_rate ?? 150;

  // Recalculate stats based on what-if parameters
  const computed = useMemo(() => {
    if (!data) return null;

    let totalEstimatedManual = 0;
    let totalEstimatedManualHours = 0;
    let totalAgentCost = 0;

    const recentTasks = data.recent_tasks.map((t) => {
      // If the task has a custom template estimated_hours (different from API benchmark),
      // it's template-customized and shouldn't be affected by slider
      const isTemplateCustom = t.estimated_manual_hours !== data.benchmark_hours_per_task;
      const taskHours = isTemplateCustom ? t.estimated_manual_hours : effectiveHours;
      const taskRate = isTemplateCustom ? (t.estimated_manual_rmb / t.estimated_manual_hours) : effectiveRate;
      const estimatedManual = taskHours * taskRate;
      const savings = estimatedManual - t.agent_cost_rmb;

      totalEstimatedManual += estimatedManual;
      totalEstimatedManualHours += taskHours;
      totalAgentCost += t.agent_cost_rmb;

      return {
        ...t,
        estimated_manual_rmb: Math.round(estimatedManual * 100) / 100,
        savings_rmb: Math.round(savings * 100) / 100,
        estimated_manual_hours: taskHours,
        _isTemplateCustom: isTemplateCustom,
      };
    });

    // Scale totals for all tasks (recent_tasks may be capped at 20)
    const taskCount = data.total_tasks_completed;
    const recentCount = data.recent_tasks.length;
    const scale = recentCount > 0 ? taskCount / recentCount : 1;

    const scaledTotalEstimatedManual = totalEstimatedManual * scale;
    const scaledTotalAgentCost = totalAgentCost * scale;
    const scaledTotalSavings = scaledTotalEstimatedManual - scaledTotalAgentCost;
    const roiRatio = scaledTotalAgentCost > 0 ? scaledTotalSavings / scaledTotalAgentCost : 0;
    const scaledManualHours = totalEstimatedManualHours * scale;
    const timeSaved = scaledManualHours - data.total_agent_hours;

    return {
      totalSavings: Math.round(scaledTotalSavings * 100) / 100,
      roiRatio: Math.round(roiRatio * 100) / 100,
      timeSaved: Math.round(timeSaved * 100) / 100,
      totalEstimatedManual: Math.round(scaledTotalEstimatedManual * 100) / 100,
      recentTasks,
    };
  }, [data, effectiveHours, effectiveRate]);

  const isWhatIfActive = whatIfHours !== null || whatIfRate !== null;

  const displaySavings = isWhatIfActive && computed ? computed.totalSavings : data?.total_savings_rmb ?? 0;
  const displayRoi = isWhatIfActive && computed ? computed.roiRatio : data?.roi_ratio ?? 0;
  const displayTimeSaved = isWhatIfActive && computed ? computed.timeSaved : data?.time_saved_hours ?? 0;
  const displayEstimatedManual = isWhatIfActive && computed ? computed.totalEstimatedManual : data?.total_estimated_manual_rmb ?? 0;
  const displayRecentTasks = isWhatIfActive && computed ? computed.recentTasks : data?.recent_tasks ?? [];

  const chartOption = data
    ? {
        tooltip: { trigger: 'axis' as const },
        legend: { data: ['Agent 成本', '评估人工成本'] },
        xAxis: { type: 'category' as const, data: ['成本对比'] },
        yAxis: { type: 'value' as const, name: '¥ RMB' },
        series: [
          {
            name: 'Agent 成本',
            type: 'bar',
            data: [data.total_agent_cost_rmb],
            itemStyle: { color: '#1890ff' },
            barWidth: 60,
          },
          {
            name: '评估人工成本',
            type: 'bar',
            data: [displayEstimatedManual],
            itemStyle: { color: '#ff7a45' },
            barWidth: 60,
          },
        ],
      }
    : {};

  type DisplayTask = ROITaskBreakdown & { _isTemplateCustom?: boolean };

  const taskColumns: ColumnsType<DisplayTask> = [
    { title: '标题', dataIndex: 'title', key: 'title', ellipsis: true },
    {
      title: 'Agent 成本',
      dataIndex: 'agent_cost_rmb',
      key: 'agent_cost_rmb',
      render: (v: number) => formatCost(v),
      width: 110,
    },
    {
      title: '评估人工成本',
      dataIndex: 'estimated_manual_rmb',
      key: 'estimated_manual_rmb',
      render: (v: number, record: DisplayTask) => (
        <span>
          {formatCost(v)}
          {record._isTemplateCustom && <Tag color="blue" style={{ marginLeft: 4, fontSize: 10 }}>自定义配置</Tag>}
        </span>
      ),
      width: 150,
    },
    {
      title: '节省',
      dataIndex: 'savings_rmb',
      key: 'savings_rmb',
      render: (v: number) => (
        <Text type={v > 0 ? 'success' : 'danger'}>{formatCost(v)}</Text>
      ),
      width: 110,
    },
    {
      title: 'Agent 耗时',
      dataIndex: 'agent_duration_minutes',
      key: 'agent_duration_minutes',
      render: (v: number) => `${v.toFixed(1)} 分钟`,
      width: 110,
    },
  ];

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <Title level={4} style={{ margin: 0 }}>ROI 大盘</Title>
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
                  title="总节省金额"
                  value={displaySavings}
                  prefix="¥"
                  precision={2}
                  valueStyle={{ color: '#3f8600' }}
                />
              </Card>
            </Col>
            <Col xs={12} sm={6}>
              <Card size="small">
                <Statistic
                  title="投资回报率 (ROI Ratio)"
                  value={displayRoi}
                  suffix="x"
                  precision={1}
                  valueStyle={{ color: '#1890ff' }}
                />
              </Card>
            </Col>
            <Col xs={12} sm={6}>
              <Card size="small">
                <Statistic
                  title="节省时间"
                  value={displayTimeSaved}
                  suffix="h"
                  precision={1}
                />
              </Card>
            </Col>
            <Col xs={12} sm={6}>
              <Card size="small">
                <Statistic title="自动化任务数" value={data.total_tasks_completed} />
              </Card>
            </Col>
          </Row>

          {/* What-If Analysis Slider */}
          <Card title="成本效益测算 (What-If)" style={{ marginBottom: 24 }}>
            <Row gutter={48}>
              <Col xs={24} sm={12}>
                <Text strong>每个任务平均耗时: {effectiveHours}h</Text>
                <Slider
                  min={1}
                  max={40}
                  step={0.5}
                  value={effectiveHours}
                  onChange={(v) => setWhatIfHours(v)}
                  marks={{ 1: '1h', 8: '8h', 20: '20h', 40: '40h' }}
                />
              </Col>
              <Col xs={24} sm={12}>
                <Text strong>人工时薪: ¥{effectiveRate}/h</Text>
                <Slider
                  min={50}
                  max={500}
                  step={10}
                  value={effectiveRate}
                  onChange={(v) => setWhatIfRate(v)}
                  marks={{ 50: '¥50', 150: '¥150', 300: '¥300', 500: '¥500' }}
                />
              </Col>
            </Row>
            {isWhatIfActive && (
              <Text type="secondary" style={{ marginTop: 8, display: 'block' }}>
                滑块设置将覆盖未配置特定人工耗时估算的模板全局预设基准。{' '}
                <a onClick={() => { setWhatIfHours(null); setWhatIfRate(null); }}>重置</a>
              </Text>
            )}
          </Card>

          <Card title="成本对比" style={{ marginBottom: 24 }}>
            {data.total_tasks_completed > 0 ? (
              <ReactECharts option={chartOption} style={{ height: 300 }} />
            ) : (
              <Empty description="在此期间暂无已完成的任务" />
            )}
          </Card>

          <Card title="Agent 角色效能" style={{ marginBottom: 24 }}>
            <Table<AgentRoleEfficiency>
              dataSource={data.by_role}
              columns={roleColumns}
              rowKey="role"
              pagination={false}
              size="small"
            />
          </Card>

          <Card title="近期任务 ROI" style={{ marginBottom: 24 }}>
            <Table<DisplayTask>
              dataSource={displayRecentTasks}
              columns={taskColumns}
              rowKey="task_id"
              pagination={false}
              size="small"
            />
          </Card>

          <Text type="secondary">
            成本基准预设: {data.benchmark_hours_per_task}h/任务 @ ¥{data.benchmark_hourly_rate}/h (可通过 .env 配置)
          </Text>
        </>
      ) : (
        <Empty description="暂无 ROI 数据" />
      )}
    </div>
  );
};

export default ROIPage;
