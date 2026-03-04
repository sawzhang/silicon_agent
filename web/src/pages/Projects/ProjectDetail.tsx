import React from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Button, Spin, Tabs, Result } from 'antd';
import { ArrowLeftOutlined } from '@ant-design/icons';
import { useProject } from '@/hooks/useProjects';
import ProjectInfoTab from './tabs/ProjectInfoTab';
import IntegrationConfigTab from './tabs/IntegrationConfigTab';
import TriggerRulesTab from './tabs/TriggerRulesTab';
import TriggerEventsTab from './tabs/TriggerEventsTab';
import MockWebhookTab from './tabs/MockWebhookTab';

const ProjectDetail: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { data: project, isLoading, refetch } = useProject(id!);

  if (isLoading) {
    return <Spin size="large" style={{ display: 'block', margin: '200px auto' }} />;
  }

  if (!project) {
    return (
      <Result
        status="404"
        title="项目不存在"
        extra={<Button onClick={() => navigate('/projects')}>返回项目列表</Button>}
      />
    );
  }

  const tabItems = [
    {
      key: 'info',
      label: '基本信息',
      children: <ProjectInfoTab project={project} onRefresh={refetch} />,
    },
    {
      key: 'integrations',
      label: '集成配置',
      children: <IntegrationConfigTab projectId={project.id} />,
    },
    {
      key: 'triggers',
      label: '触发规则',
      children: <TriggerRulesTab projectId={project.id} />,
    },
    {
      key: 'events',
      label: '事件历史',
      children: <TriggerEventsTab projectId={project.id} />,
    },
    {
      key: 'mock-webhook',
      label: '模拟触发',
      children: <MockWebhookTab projectId={project.id} />,
    },
  ];

  return (
    <div>
      <div style={{ marginBottom: 16 }}>
        <Button
          type="link"
          icon={<ArrowLeftOutlined />}
          onClick={() => navigate('/projects')}
          style={{ paddingLeft: 0 }}
        >
          返回项目列表
        </Button>
        <span style={{ fontSize: 20, fontWeight: 600, marginLeft: 8 }}>
          {project.display_name}
        </span>
      </div>
      <Tabs items={tabItems} />
    </div>
  );
};

export default ProjectDetail;
