import React, { useState } from 'react';
import { Button, Tag, message, Popconfirm } from 'antd';
import { PlusOutlined, DeleteOutlined } from '@ant-design/icons';
import { ProTable } from '@ant-design/pro-components';
import type { ActionType, ProColumns } from '@ant-design/pro-components';
import { ModalForm, ProFormText, ProFormTextArea } from '@ant-design/pro-components';
import { listProjects } from '@/services/projectApi';
import { useCreateProject, useDeleteProject } from '@/hooks/useProjects';
import { formatTimestamp } from '@/utils/formatters';
import type { Project, ProjectCreateRequest } from '@/types/project';

const STATUS_COLOR: Record<string, string> = {
  active: 'success',
  archived: 'default',
};

const ProjectList: React.FC = () => {
  const actionRef = React.useRef<ActionType>();
  const createProject = useCreateProject();
  const deleteProject = useDeleteProject();
  const [createOpen, setCreateOpen] = useState(false);

  const columns: ProColumns<Project>[] = [
    {
      title: 'Name',
      dataIndex: 'display_name',
      ellipsis: true,
    },
    {
      title: 'Code',
      dataIndex: 'name',
      width: 160,
      search: false,
    },
    {
      title: 'Repo',
      dataIndex: 'repo_url',
      ellipsis: true,
      search: false,
      render: (_, record) =>
        record.repo_url ? (
          <a href={record.repo_url} target="_blank" rel="noreferrer">
            {record.repo_url}
          </a>
        ) : (
          '-'
        ),
    },
    {
      title: 'Branch',
      dataIndex: 'branch',
      width: 100,
      search: false,
    },
    {
      title: 'Status',
      dataIndex: 'status',
      width: 100,
      valueEnum: { active: 'Active', archived: 'Archived' },
      render: (_, record) => <Tag color={STATUS_COLOR[record.status]}>{record.status}</Tag>,
    },
    {
      title: 'Created',
      dataIndex: 'created_at',
      width: 180,
      search: false,
      render: (_, record) => formatTimestamp(record.created_at),
    },
    {
      title: 'Action',
      width: 80,
      search: false,
      render: (_, record) => (
        <Popconfirm
          title="Delete this project?"
          onConfirm={async () => {
            await deleteProject.mutateAsync(record.id);
            message.success('Project deleted');
            actionRef.current?.reload();
          }}
        >
          <Button type="link" danger icon={<DeleteOutlined />} size="small" />
        </Popconfirm>
      ),
    },
  ];

  return (
    <>
      <ProTable<Project>
        headerTitle="Projects"
        actionRef={actionRef}
        rowKey="id"
        columns={columns}
        request={async (params) => {
          const res = await listProjects({
            status: params.status,
            page: params.current,
            page_size: params.pageSize,
          });
          return { data: res.items, total: res.total, success: true };
        }}
        toolBarRender={() => [
          <Button key="create" type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
            New Project
          </Button>,
        ]}
        pagination={{ defaultPageSize: 20 }}
      />
      <ModalForm
        title="Create Project"
        open={createOpen}
        onOpenChange={setCreateOpen}
        onFinish={async (values: Record<string, string>) => {
          await createProject.mutateAsync({
            name: values.name,
            display_name: values.display_name,
            repo_url: values.repo_url || undefined,
            branch: values.branch || 'main',
            description: values.description || undefined,
          });
          message.success('Project created');
          actionRef.current?.reload();
          return true;
        }}
      >
        <ProFormText name="name" label="Project Code" placeholder="e.g. silicon-agent" rules={[{ required: true }]} />
        <ProFormText name="display_name" label="Display Name" placeholder="e.g. Silicon Agent" rules={[{ required: true }]} />
        <ProFormText name="repo_url" label="Repository URL" placeholder="https://github.com/org/repo" />
        <ProFormText name="branch" label="Branch" initialValue="main" />
        <ProFormTextArea name="description" label="Description" />
      </ModalForm>
    </>
  );
};

export default ProjectList;
