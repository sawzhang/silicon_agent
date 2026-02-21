import React from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Card, Descriptions, Tag, Button, Spin, Space, Typography } from 'antd';
import { ArrowLeftOutlined } from '@ant-design/icons';
import { useSkill } from '@/hooks/useSkills';
import { formatTimestamp } from '@/utils/formatters';

const { Title } = Typography;

const LAYER_COLOR: Record<string, string> = {
  foundation: 'blue',
  domain: 'green',
  scenario: 'orange',
};

const SkillDetail: React.FC = () => {
  const { name } = useParams<{ name: string }>();
  const navigate = useNavigate();
  const { data: skill, isLoading } = useSkill(name!);

  if (isLoading || !skill) {
    return <Spin size="large" style={{ display: 'block', margin: '100px auto' }} />;
  }

  return (
    <div>
      <Space style={{ marginBottom: 16 }}>
        <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/skills')}>
          Back
        </Button>
      </Space>

      <Title level={4}>{skill.display_name}</Title>

      <Card title="Skill Details" style={{ marginBottom: 16 }}>
        <Descriptions column={2}>
          <Descriptions.Item label="Name">{skill.name}</Descriptions.Item>
          <Descriptions.Item label="Layer">
            <Tag color={LAYER_COLOR[skill.layer]}>{skill.layer}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="Version">{skill.version}</Descriptions.Item>
          <Descriptions.Item label="Status">
            <Tag color={skill.enabled ? 'green' : 'default'}>{skill.enabled ? 'Enabled' : 'Disabled'}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="Tags" span={2}>
            {skill.tags.map((t) => <Tag key={t}>{t}</Tag>)}
          </Descriptions.Item>
          <Descriptions.Item label="Applicable Roles" span={2}>
            {skill.applicable_roles.map((r) => <Tag key={r}>{r}</Tag>)}
          </Descriptions.Item>
          <Descriptions.Item label="Created">{formatTimestamp(skill.created_at)}</Descriptions.Item>
          <Descriptions.Item label="Updated">{formatTimestamp(skill.updated_at)}</Descriptions.Item>
        </Descriptions>
      </Card>

      <Card title="Description">
        <Typography.Paragraph>{skill.description}</Typography.Paragraph>
      </Card>

      <Card title="Configuration" style={{ marginTop: 16 }}>
        <pre style={{ fontSize: 12 }}>{JSON.stringify(skill.config, null, 2)}</pre>
      </Card>
    </div>
  );
};

export default SkillDetail;
