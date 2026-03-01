import React, { useState } from 'react';
import { Button, Tag, message, Popconfirm, Space, Tooltip } from 'antd';
import { PlusOutlined, DeleteOutlined, SyncOutlined } from '@ant-design/icons';
import { ProTable } from '@ant-design/pro-components';
import type { ActionType, ProColumns } from '@ant-design/pro-components';
import { ModalForm, ProFormText, ProFormTextArea } from '@ant-design/pro-components';
import { listProjects, syncProject } from '@/services/projectApi';
import { useCreateProject, useDeleteProject } from '@/hooks/useProjects';
import { formatTimestamp } from '@/utils/formatters';
import type { Project, ProjectCreateRequest } from '@/types/project';

const STATUS_COLOR: Record<string, string> = {
  active: 'success',
  archived: 'default',
};

const TECH_COLORS: Record<string, string> = {
  'Python': 'blue',
  'FastAPI': 'cyan',
  'React': 'geekblue',
  'TypeScript': 'purple',
  'Node.js': 'green',
  'Docker': 'volcano',
  'Go': 'lime',
  'Rust': 'orange',
  'Java': 'red',
  'Vue.js': 'green',
  'Next.js': 'geekblue',
  'SQLAlchemy': 'gold',
};

const ProjectList: React.FC = () => {
  const actionRef = React.useRef<ActionType>();
  const createProject = useCreateProject();
  const deleteProject = useDeleteProject();
  const [createOpen, setCreateOpen] = useState(false);
  const [syncingIds, setSyncingIds] = useState<Set<string>>(new Set());

  const handleSync = async (projectId: string) => {
    setSyncingIds((prev) => new Set(prev).add(projectId));
    try {
      const result = await syncProject(projectId);
      message.success(`同步成功：${result.tech_stack.join(', ') || '未检测到技术栈'}`);
      actionRef.current?.reload();
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '同步失败');
    } finally {
      setSyncingIds((prev) => {
        const next = new Set(prev);
        next.delete(projectId);
        return next;
      });
    }
  };

  const columns: ProColumns<Project>[] = [
    {
      title: '名称',
      dataIndex: 'display_name',
      ellipsis: true,
    },
    {
      title: '标识代码',
      dataIndex: 'name',
      width: 160,
      search: false,
    },
    {
      title: '代码库地址',
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
      title: '技术栈',
      dataIndex: 'tech_stack',
      width: 220,
      search: false,
      render: (_, record) =>
        record.tech_stack && record.tech_stack.length > 0 ? (
          <Space size={[0, 4]} wrap>
            {record.tech_stack.map((tech) => (
              <Tag key={tech} color={TECH_COLORS[tech] || 'default'}>
                {tech}
              </Tag>
            ))}
          </Space>
        ) : (
          '-'
        ),
    },
    {
      title: '分支',
      dataIndex: 'branch',
      width: 100,
      search: false,
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 100,
      valueEnum: { active: '活跃', archived: '已归档' },
      render: (_, record) => <Tag color={STATUS_COLOR[record.status]}>{record.status === 'active' ? '活跃' : '已归档'}</Tag>,
    },
    {
      title: '最后同步',
      dataIndex: 'last_synced_at',
      width: 160,
      search: false,
      render: (_, record) => record.last_synced_at ? formatTimestamp(record.last_synced_at) : '-',
    },
    {
      title: '操作',
      width: 120,
      search: false,
      render: (_, record) => (
        <Space size="small">
          <Tooltip title={record.repo_url ? '同步代码库信息' : '未配置代码库地址'}>
            <Button
              type="link"
              icon={<SyncOutlined spin={syncingIds.has(record.id)} />}
              size="small"
              disabled={!record.repo_url}
              onClick={() => handleSync(record.id)}
            />
          </Tooltip>
          <Popconfirm
            title="确认删除此项目？"
            onConfirm={async () => {
              await deleteProject.mutateAsync(record.id);
              message.success('项目已删除');
              actionRef.current?.reload();
            }}
          >
            <Button type="link" danger icon={<DeleteOutlined />} size="small" />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <>
      <ProTable<Project>
        headerTitle="项目管理"
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
            新建项目
          </Button>,
        ]}
        pagination={{ defaultPageSize: 20 }}
      />
      <ModalForm
        title="新建项目"
        open={createOpen}
        onOpenChange={setCreateOpen}
        submitter={{
          submitButtonProps: {
            loading: createProject.isPending,
          },
        }}
        onFinish={async (values: Record<string, string>) => {
          try {
            await createProject.mutateAsync({
              name: values.name,
              display_name: values.display_name,
              repo_url: values.repo_url || undefined,
              branch: values.branch || 'main',
              description: values.description || undefined,
            });
            message.success('项目创建成功');
            actionRef.current?.reload();
            return true;
          } catch (err: any) {
            message.error(err?.response?.data?.detail || '项目创建失败');
            return false;
          }
        }}
      >
        <ProFormText name="name" label="项目标识" placeholder="例如：silicon-agent" rules={[{ required: true }]} />
        <ProFormText name="display_name" label="显示名称" placeholder="例如：Silicon Agent" rules={[{ required: true }]} />
        <ProFormText name="repo_url" label="代码库地址" placeholder="https://github.com/org/repo" />
        <ProFormText name="branch" label="目标分支" initialValue="main" />
        <ProFormTextArea name="description" label="项目描述" />
      </ModalForm>
    </>
  );
};

export default ProjectList;
