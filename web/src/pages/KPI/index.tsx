import React, { useState } from 'react';
import { Row, Col, Typography, Spin, Select, Card, Empty, Statistic } from 'antd';
import { useKPISummary, useKPITimeSeries } from '@/hooks/useKPI';
import KPICard from '@/components/KPICard';
import KPIChart from '@/components/KPIChart';
import { KPI_DEFINITIONS } from '@/utils/constants';

const { Title } = Typography;

const KPIPage: React.FC = () => {
  const [selectedMetric, setSelectedMetric] = useState(KPI_DEFINITIONS[0].name);
  const { data: summary, isLoading: summaryLoading } = useKPISummary();
  const { data: timeseries, isLoading: tsLoading } = useKPITimeSeries(selectedMetric);

  return (
    <div>
      <Title level={4} style={{ marginBottom: 16 }}>KPI 监控仪表盘</Title>

      {summaryLoading ? (
        <Spin size="large" style={{ display: 'block', margin: '40px auto' }} />
      ) : summary ? (
        <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
          <Col xs={12} sm={8} md={6}>
            <Card size="small">
              <Statistic title="任务总数" value={summary.total_tasks} />
            </Card>
          </Col>
          <Col xs={12} sm={8} md={6}>
            <Card size="small">
              <Statistic title="已完成" value={summary.completed_tasks} />
            </Card>
          </Col>
          <Col xs={12} sm={8} md={6}>
            <Card size="small">
              <Statistic title="成功率" value={summary.success_rate} suffix="%" />
            </Card>
          </Col>
          <Col xs={12} sm={8} md={6}>
            <Card size="small">
              <Statistic title="总计 Tokens" value={summary.total_tokens} />
            </Card>
          </Col>
          <Col xs={12} sm={8} md={6}>
            <Card size="small">
              <Statistic title="总成本" value={summary.total_cost_rmb} prefix="¥" precision={2} />
            </Card>
          </Col>
          <Col xs={12} sm={8} md={6}>
            <Card size="small">
              <Statistic title="平均耗时" value={summary.avg_duration_minutes} suffix="分钟" precision={1} />
            </Card>
          </Col>
          {summary.metrics.length > 0 && summary.metrics.map((m) => (
            <Col key={m.metric_name} xs={12} sm={8} md={6}>
              <KPICard metric={m} />
            </Col>
          ))}
        </Row>
      ) : (
        <Empty description="暂无 KPI 数据" style={{ marginBottom: 24 }} />
      )}

      <Card
        title="时序数据"
        extra={
          <Select
            value={selectedMetric}
            onChange={setSelectedMetric}
            style={{ width: 180 }}
            options={KPI_DEFINITIONS.map((k) => ({ label: k.display_name, value: k.name }))}
          />
        }
      >
        {tsLoading ? (
          <Spin style={{ display: 'block', margin: '40px auto' }} />
        ) : timeseries && timeseries.data.length > 0 ? (
          <KPIChart data={timeseries} height={400} />
        ) : (
          <Empty description="暂无时序数据" />
        )}
      </Card>
    </div>
  );
};

export default KPIPage;
