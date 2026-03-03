import React, { useState } from 'react';
import { Descriptions, Button, Form, Input, Select, message, Space } from 'antd';
import { EditOutlined } from '@ant-design/icons';
import { useUpdateProject } from '@/hooks/useProjects';
import { formatTimestamp } from '@/utils/formatters';
import type { Project } from '@/types/project';

interface Props {
  project: Project;
  onRefresh: () => void;
}

const ProjectInfoTab: React.FC<Props> = ({ project, onRefresh }) => {
  const [editing, setEditing] = useState(false);
  const [form] = Form.useForm();
  const updateProject = useUpdateProject();

  const handleSave = async () => {
    try {
      const values = await form.validateFields();
      await updateProject.mutateAsync({
        id: project.id,
        req: {
          display_name: values.display_name,
          repo_url: values.repo_url || undefined,
          branch: values.branch || undefined,
          description: values.description || undefined,
          status: values.status,
        },
      });
      message.success('项目信息已更新');
      setEditing(false);
      onRefresh();
    } catch (err: unknown) {
      const error = err as { response?: { data?: { detail?: string } } };
      message.error(error?.response?.data?.detail || '更新失败');
    }
  };

  if (editing) {
    return (
      <Form
        form={form}
        layout="vertical"
        initialValues={{
          display_name: project.display_name,
          repo_url: project.repo_url || '',
          branch: project.branch,
          description: project.description || '',
          status: project.status,
        }}
      >
        <Form.Item name="display_name" label="显示名称" rules={[{ required: true }]}>
          <Input />
        </Form.Item>
        <Form.Item name="repo_url" label="代码库地址">
          <Input />
        </Form.Item>
        <Form.Item name="branch" label="目标分支">
          <Input />
        </Form.Item>
        <Form.Item name="status" label="状态" rules={[{ required: true }]}>
          <Select
            options={[
              { label: '活跃', value: 'active' },
              { label: '已归档', value: 'archived' },
            ]}
          />
        </Form.Item>
        <Form.Item name="description" label="项目描述">
          <Input.TextArea rows={3} />
        </Form.Item>
        <Space>
          <Button type="primary" onClick={handleSave} loading={updateProject.isPending}>
            保存
          </Button>
          <Button onClick={() => setEditing(false)}>取消</Button>
        </Space>
      </Form>
    );
  }

  return (
    <>
      <Button
        type="link"
        icon={<EditOutlined />}
        onClick={() => setEditing(true)}
        style={{ float: 'right' }}
      >
        编辑
      </Button>
      <Descriptions column={2} bordered size="small">
        <Descriptions.Item label="项目标识">{project.name}</Descriptions.Item>
        <Descriptions.Item label="显示名称">{project.display_name}</Descriptions.Item>
        <Descriptions.Item label="代码库地址" span={2}>
          {project.repo_url ? (
            <a href={project.repo_url} target="_blank" rel="noreferrer">
              {project.repo_url}
            </a>
          ) : (
            '-'
          )}
        </Descriptions.Item>
        <Descriptions.Item label="目标分支">{project.branch || '-'}</Descriptions.Item>
        <Descriptions.Item label="状态">{project.status === 'active' ? '活跃' : '已归档'}</Descriptions.Item>
        <Descriptions.Item label="技术栈" span={2}>
          {project.tech_stack?.join(', ') || '-'}
        </Descriptions.Item>
        <Descriptions.Item label="项目描述" span={2}>
          {project.description || '-'}
        </Descriptions.Item>
        <Descriptions.Item label="最后同步">
          {project.last_synced_at ? formatTimestamp(project.last_synced_at) : '-'}
        </Descriptions.Item>
        <Descriptions.Item label="创建时间">{formatTimestamp(project.created_at)}</Descriptions.Item>
      </Descriptions>
    </>
  );
};

export default ProjectInfoTab;
