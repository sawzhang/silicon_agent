import React, { useState } from 'react';
import { Button, Tag, message } from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import { ProTable } from '@ant-design/pro-components';
import type { ActionType, ProColumns } from '@ant-design/pro-components';
import { Link } from 'react-router-dom';
import { listTasks } from '@/services/taskApi';
import { useCreateTask } from '@/hooks/useTasks';
import { formatTimestamp, formatCost } from '@/utils/formatters';
import type { Task, TaskCreateRequest } from '@/types/task';
import { ModalForm, ProFormText, ProFormTextArea, ProFormSelect } from '@ant-design/pro-components';

const STATUS_COLOR: Record<string, string> = {
  queued: 'default',
  running: 'processing',
  completed: 'success',
  failed: 'error',
  cancelled: 'warning',
};

const PRIORITY_COLOR: Record<string, string> = {
  low: 'default',
  medium: 'blue',
  high: 'orange',
  critical: 'red',
};

const TaskList: React.FC = () => {
  const actionRef = React.useRef<ActionType>();
  const createTask = useCreateTask();
  const [createOpen, setCreateOpen] = useState(false);

  const columns: ProColumns<Task>[] = [
    {
      title: 'ID',
      dataIndex: 'id',
      width: 100,
      render: (_, record) => <Link to={`/tasks/${record.id}`}>{record.id.slice(0, 8)}...</Link>,
      search: false,
    },
    { title: 'Title', dataIndex: 'title', ellipsis: true },
    {
      title: 'Status',
      dataIndex: 'status',
      valueEnum: { queued: 'Queued', running: 'Running', completed: 'Completed', failed: 'Failed', cancelled: 'Cancelled' },
      render: (_, record) => <Tag color={STATUS_COLOR[record.status]}>{record.status}</Tag>,
    },
    {
      title: 'Priority',
      dataIndex: 'priority',
      valueEnum: { low: 'Low', medium: 'Medium', high: 'High', critical: 'Critical' },
      render: (_, record) => <Tag color={PRIORITY_COLOR[record.priority]}>{record.priority}</Tag>,
    },
    {
      title: 'Cost',
      dataIndex: 'total_cost_usd',
      search: false,
      render: (_, record) => formatCost(record.total_cost_usd),
    },
    {
      title: 'Created',
      dataIndex: 'created_at',
      valueType: 'dateRange',
      render: (_, record) => formatTimestamp(record.created_at),
    },
  ];

  return (
    <>
      <ProTable<Task>
        headerTitle="Task Pipeline"
        actionRef={actionRef}
        rowKey="id"
        columns={columns}
        request={async (params) => {
          const res = await listTasks({
            status: params.status,
            page: params.current,
            page_size: params.pageSize,
          });
          return { data: res.tasks, total: res.total, success: true };
        }}
        toolBarRender={() => [
          <Button key="create" type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
            New Task
          </Button>,
        ]}
        pagination={{ defaultPageSize: 20 }}
      />
      <ModalForm
        title="Create Task"
        open={createOpen}
        onOpenChange={setCreateOpen}
        onFinish={async (values: Record<string, string>) => {
          await createTask.mutateAsync({
            title: values.title,
            description: values.description,
            priority: values.priority as TaskCreateRequest['priority'],
          });
          message.success('Task created');
          actionRef.current?.reload();
          return true;
        }}
      >
        <ProFormText name="title" label="Title" rules={[{ required: true }]} />
        <ProFormTextArea name="description" label="Description" rules={[{ required: true }]} />
        <ProFormSelect
          name="priority"
          label="Priority"
          initialValue="medium"
          options={[
            { label: 'Low', value: 'low' },
            { label: 'Medium', value: 'medium' },
            { label: 'High', value: 'high' },
            { label: 'Critical', value: 'critical' },
          ]}
        />
      </ModalForm>
    </>
  );
};

export default TaskList;
