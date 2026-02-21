import React from 'react';
import { Tag, Button } from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import { ProTable } from '@ant-design/pro-components';
import { Link } from 'react-router-dom';
import type { ProColumns } from '@ant-design/pro-components';
import { listSkills } from '@/services/skillApi';
import type { Skill } from '@/types/skill';

const LAYER_COLOR: Record<string, string> = {
  foundation: 'blue',
  domain: 'green',
  scenario: 'orange',
};

const SkillList: React.FC = () => {
  const columns: ProColumns<Skill>[] = [
    {
      title: 'Name',
      dataIndex: 'name',
      render: (_, record) => <Link to={`/skills/${record.name}`}>{record.display_name}</Link>,
    },
    {
      title: 'Layer',
      dataIndex: 'layer',
      valueEnum: { foundation: 'Foundation', domain: 'Domain', scenario: 'Scenario' },
      render: (_, record) => <Tag color={LAYER_COLOR[record.layer]}>{record.layer}</Tag>,
    },
    {
      title: 'Tags',
      dataIndex: 'tags',
      search: false,
      render: (_, record) => record.tags.map((t) => <Tag key={t}>{t}</Tag>),
    },
    {
      title: 'Roles',
      dataIndex: 'applicable_roles',
      search: false,
      render: (_, record) => record.applicable_roles.map((r) => <Tag key={r}>{r}</Tag>),
    },
    {
      title: 'Status',
      dataIndex: 'enabled',
      search: false,
      render: (_, record) => <Tag color={record.enabled ? 'green' : 'default'}>{record.enabled ? 'Enabled' : 'Disabled'}</Tag>,
    },
    {
      title: 'Version',
      dataIndex: 'version',
      search: false,
    },
  ];

  return (
    <ProTable<Skill>
      headerTitle="Skills Management"
      rowKey="name"
      columns={columns}
      request={async (params) => {
        const data = await listSkills({
          layer: params.layer,
        });
        return { data, total: data.length, success: true };
      }}
      toolBarRender={() => [
        <Button key="create" type="primary" icon={<PlusOutlined />}>
          New Skill
        </Button>,
      ]}
    />
  );
};

export default SkillList;
