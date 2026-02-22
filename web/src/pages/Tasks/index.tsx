import React, { useState, useRef } from 'react';
import {
  Button, Tag, message, Modal, Radio, Input, Table, Space, Popconfirm, Alert, Spin,
} from 'antd';
import {
  PlusOutlined, DeleteOutlined, RobotOutlined, FormOutlined, PlusCircleOutlined,
} from '@ant-design/icons';
import { ProTable } from '@ant-design/pro-components';
import type { ActionType, ProColumns } from '@ant-design/pro-components';
import {
  ModalForm, ProFormText, ProFormTextArea, ProFormSelect,
} from '@ant-design/pro-components';
import { Link } from 'react-router-dom';
import { listTasks, decomposePrd, batchCreateTasks, createTask } from '@/services/taskApi';
import { useTemplateList } from '@/hooks/useTemplates';
import { useProjectList } from '@/hooks/useProjects';
import { formatTimestamp, formatCost } from '@/utils/formatters';
import type { Task, DecomposedTask } from '@/types/task';
import { STAGE_NAMES } from '@/utils/constants';

const STATUS_COLOR: Record<string, string> = {
  pending: 'default',
  running: 'processing',
  completed: 'success',
  failed: 'error',
  cancelled: 'warning',
};

const PRIORITY_COLOR: Record<string, string> = {
  high: 'red',
  medium: 'orange',
  low: 'green',
};

const STAGE_DISPLAY: Record<string, string> = Object.fromEntries(
  STAGE_NAMES.map((s) => [s.key, s.name])
);

type CreateMode = 'single' | 'prd';

interface EditableTask extends DecomposedTask {
  key: string;
}

const TaskList: React.FC = () => {
  const actionRef = useRef<ActionType>();
  const { data: templateData } = useTemplateList();
  const { data: projectData } = useProjectList();

  // Wizard state
  const [wizardOpen, setWizardOpen] = useState(false);
  const [step, setStep] = useState(0);
  const [createMode, setCreateMode] = useState<CreateMode>('single');

  // Single mode state
  const [singleForm, setSingleForm] = useState({
    title: '', description: '', template_id: '', project_id: '',
  });

  // PRD mode state
  const [prdText, setPrdText] = useState('');
  const [prdProjectId, setPrdProjectId] = useState<string>('');
  const [prdTemplateId, setPrdTemplateId] = useState<string>('');
  const [decomposedTasks, setDecomposedTasks] = useState<EditableTask[]>([]);
  const [decomposeSummary, setDecomposeSummary] = useState('');
  const [analyzing, setAnalyzing] = useState(false);
  const [creating, setCreating] = useState(false);

  const resetWizard = () => {
    setStep(0);
    setCreateMode('single');
    setSingleForm({ title: '', description: '', template_id: '', project_id: '' });
    setPrdText('');
    setPrdProjectId('');
    setPrdTemplateId('');
    setDecomposedTasks([]);
    setDecomposeSummary('');
    setAnalyzing(false);
    setCreating(false);
  };

  const handleOpenWizard = () => {
    resetWizard();
    setWizardOpen(true);
  };

  const handleCloseWizard = () => {
    setWizardOpen(false);
    resetWizard();
  };

  // Handle PRD decompose
  const handleDecompose = async () => {
    if (!prdText.trim()) {
      message.warning('Please paste PRD content first');
      return;
    }
    setAnalyzing(true);
    try {
      const result = await decomposePrd({
        prd_text: prdText,
        project_id: prdProjectId || undefined,
        template_id: prdTemplateId || undefined,
      });
      setDecomposedTasks(
        result.tasks.map((t, i) => ({ ...t, key: `task-${i}` }))
      );
      setDecomposeSummary(result.summary);
      message.success(`AI analysis complete: ${result.tasks.length} subtasks identified`);
      setStep(2);
    } catch (err: any) {
      message.error(err?.response?.data?.detail || 'Analysis failed, please retry');
    } finally {
      setAnalyzing(false);
    }
  };

  // Handle single task create
  const handleCreateSingle = async () => {
    if (!singleForm.title.trim()) {
      message.warning('Title is required');
      return;
    }
    if (!singleForm.description.trim()) {
      message.warning('Description is required');
      return;
    }
    setCreating(true);
    try {
      await createTask({
        title: singleForm.title,
        description: singleForm.description,
        template_id: singleForm.template_id || undefined,
        project_id: singleForm.project_id || undefined,
      });
      message.success('Task created');
      actionRef.current?.reload();
      handleCloseWizard();
    } catch (err: any) {
      message.error(err?.response?.data?.detail || 'Creation failed');
    } finally {
      setCreating(false);
    }
  };

  // Handle batch create
  const handleBatchCreate = async () => {
    if (decomposedTasks.length === 0) {
      message.warning('No tasks to create');
      return;
    }
    setCreating(true);
    try {
      const result = await batchCreateTasks({
        tasks: decomposedTasks.map((t) => ({
          title: t.title,
          description: t.description,
          template_id: prdTemplateId || undefined,
          project_id: prdProjectId || undefined,
        })),
      });
      message.success(`${result.created} tasks created`);
      actionRef.current?.reload();
      handleCloseWizard();
    } catch (err: any) {
      message.error(err?.response?.data?.detail || 'Batch creation failed');
    } finally {
      setCreating(false);
    }
  };

  // Edit decomposed task
  const updateTask = (key: string, field: keyof DecomposedTask, value: string) => {
    setDecomposedTasks((prev) =>
      prev.map((t) => (t.key === key ? { ...t, [field]: value } : t))
    );
  };

  const removeTask = (key: string) => {
    setDecomposedTasks((prev) => prev.filter((t) => t.key !== key));
  };

  const addTask = () => {
    setDecomposedTasks((prev) => [
      ...prev,
      { key: `task-${Date.now()}`, title: '', description: '', priority: 'medium' },
    ]);
  };

  // Build template/project options
  const templateOptions = templateData?.items?.map((t) => ({
    label: `${t.display_name} (${t.stages.length} stages)`,
    value: t.id,
  })) || [];

  const projectOptions = projectData?.items?.map((p) => ({
    label: `${p.display_name}${p.repo_url ? ' (has repo)' : ''}`,
    value: p.id,
  })) || [];

  const selectedTemplate = templateData?.items?.find((t) => t.id === (createMode === 'single' ? singleForm.template_id : prdTemplateId));

  // Table columns
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
      title: 'Template',
      dataIndex: 'template_name',
      width: 100,
      search: false,
      render: (_, record) => record.template_name ? <Tag>{record.template_name}</Tag> : '-',
    },
    {
      title: 'Project',
      dataIndex: 'project_name',
      width: 120,
      search: false,
      render: (_, record) => record.project_name || '-',
    },
    {
      title: 'Status',
      dataIndex: 'status',
      valueEnum: { pending: 'Pending', running: 'Running', completed: 'Completed', failed: 'Failed', cancelled: 'Cancelled' },
      render: (_, record) => <Tag color={STATUS_COLOR[record.status]}>{record.status}</Tag>,
    },
    {
      title: 'Tokens',
      dataIndex: 'total_tokens',
      width: 100,
      search: false,
      render: (_, record) => record.total_tokens > 0 ? record.total_tokens.toLocaleString() : '-',
    },
    {
      title: 'Cost',
      dataIndex: 'total_cost_rmb',
      width: 100,
      search: false,
      render: (_, record) => formatCost(record.total_cost_rmb),
    },
    {
      title: 'Created',
      dataIndex: 'created_at',
      valueType: 'dateRange',
      render: (_, record) => formatTimestamp(record.created_at),
    },
  ];

  // Decomposed tasks table columns
  const decomposeColumns = [
    {
      title: 'Title',
      dataIndex: 'title',
      render: (_: any, record: EditableTask) => (
        <Input
          value={record.title}
          onChange={(e) => updateTask(record.key, 'title', e.target.value)}
          placeholder="Task title"
        />
      ),
    },
    {
      title: 'Description',
      dataIndex: 'description',
      render: (_: any, record: EditableTask) => (
        <Input.TextArea
          value={record.description}
          onChange={(e) => updateTask(record.key, 'description', e.target.value)}
          autoSize={{ minRows: 2, maxRows: 6 }}
          placeholder="Task description with acceptance criteria"
        />
      ),
    },
    {
      title: 'Priority',
      dataIndex: 'priority',
      width: 100,
      render: (_: any, record: EditableTask) => (
        <Tag color={PRIORITY_COLOR[record.priority]}>{record.priority}</Tag>
      ),
    },
    {
      title: 'Action',
      width: 60,
      render: (_: any, record: EditableTask) => (
        <Popconfirm title="Remove this task?" onConfirm={() => removeTask(record.key)}>
          <Button type="link" danger icon={<DeleteOutlined />} size="small" />
        </Popconfirm>
      ),
    },
  ];

  // Render wizard content based on step
  const renderStepContent = () => {
    // Step 0: Choose mode
    if (step === 0) {
      return (
        <div style={{ textAlign: 'center', padding: '24px 0' }}>
          <Radio.Group
            value={createMode}
            onChange={(e) => setCreateMode(e.target.value)}
            size="large"
            buttonStyle="solid"
          >
            <Radio.Button value="single" style={{ height: 80, lineHeight: '60px', padding: '0 32px' }}>
              <FormOutlined style={{ marginRight: 8 }} />
              Single Task
            </Radio.Button>
            <Radio.Button value="prd" style={{ height: 80, lineHeight: '60px', padding: '0 32px' }}>
              <RobotOutlined style={{ marginRight: 8 }} />
              PRD Smart Decompose
            </Radio.Button>
          </Radio.Group>
          <div style={{ marginTop: 16, color: '#888' }}>
            {createMode === 'single'
              ? 'Create a single task with title and description'
              : 'Paste PRD content and let AI decompose it into subtasks'}
          </div>
        </div>
      );
    }

    // Step 1: Form input
    if (step === 1) {
      if (createMode === 'single') {
        return (
          <div style={{ maxWidth: 600, margin: '0 auto' }}>
            <div style={{ marginBottom: 16 }}>
              <label style={{ display: 'block', fontWeight: 500, marginBottom: 4 }}>Title *</label>
              <Input
                value={singleForm.title}
                onChange={(e) => setSingleForm((p) => ({ ...p, title: e.target.value }))}
                placeholder="Enter task title"
              />
            </div>
            <div style={{ marginBottom: 16 }}>
              <label style={{ display: 'block', fontWeight: 500, marginBottom: 4 }}>Description *</label>
              <Input.TextArea
                value={singleForm.description}
                onChange={(e) => setSingleForm((p) => ({ ...p, description: e.target.value }))}
                placeholder={'Describe the task in detail:\n- What needs to be done\n- Acceptance criteria\n- Technical constraints'}
                autoSize={{ minRows: 6, maxRows: 12 }}
              />
            </div>
            <div style={{ marginBottom: 16 }}>
              <label style={{ display: 'block', fontWeight: 500, marginBottom: 4 }}>Pipeline Template</label>
              <select
                value={singleForm.template_id}
                onChange={(e) => setSingleForm((p) => ({ ...p, template_id: e.target.value }))}
                style={{ width: '100%', padding: '4px 8px', borderRadius: 6, border: '1px solid #d9d9d9' }}
              >
                <option value="">Select a template</option>
                {templateOptions.map((o) => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
              {selectedTemplate && selectedTemplate.stages.length > 0 && (
                <div style={{ marginTop: 4, fontSize: 12, color: '#888' }}>
                  Stages: {selectedTemplate.stages.map((s: any) => STAGE_DISPLAY[s.name] || s.name).join(' → ')}
                </div>
              )}
            </div>
            <div style={{ marginBottom: 16 }}>
              <label style={{ display: 'block', fontWeight: 500, marginBottom: 4 }}>Project</label>
              <select
                value={singleForm.project_id}
                onChange={(e) => setSingleForm((p) => ({ ...p, project_id: e.target.value }))}
                style={{ width: '100%', padding: '4px 8px', borderRadius: 6, border: '1px solid #d9d9d9' }}
              >
                <option value="">Select a project</option>
                {projectOptions.map((o) => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
            </div>
          </div>
        );
      }

      // PRD mode
      return (
        <div style={{ maxWidth: 700, margin: '0 auto' }}>
          <div style={{ marginBottom: 16 }}>
            <label style={{ display: 'block', fontWeight: 500, marginBottom: 4 }}>PRD Content *</label>
            <Input.TextArea
              value={prdText}
              onChange={(e) => setPrdText(e.target.value)}
              placeholder={'Paste your PRD document here...\n\nExample:\n用户登录模块需求：\n1. 支持邮箱+密码登录\n2. 支持 OAuth2 第三方登录（Google, GitHub）\n3. 密码重置功能\n4. 登录日志审计'}
              autoSize={{ minRows: 10, maxRows: 20 }}
            />
          </div>
          <Space style={{ width: '100%' }} direction="vertical" size="middle">
            <div>
              <label style={{ display: 'block', fontWeight: 500, marginBottom: 4 }}>Project (provides repo context for better analysis)</label>
              <select
                value={prdProjectId}
                onChange={(e) => setPrdProjectId(e.target.value)}
                style={{ width: '100%', padding: '4px 8px', borderRadius: 6, border: '1px solid #d9d9d9' }}
              >
                <option value="">Select a project</option>
                {projectOptions.map((o) => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
            </div>
            <div>
              <label style={{ display: 'block', fontWeight: 500, marginBottom: 4 }}>Default Template (applied to all generated tasks)</label>
              <select
                value={prdTemplateId}
                onChange={(e) => setPrdTemplateId(e.target.value)}
                style={{ width: '100%', padding: '4px 8px', borderRadius: 6, border: '1px solid #d9d9d9' }}
              >
                <option value="">Select a template</option>
                {templateOptions.map((o) => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
            </div>
          </Space>
        </div>
      );
    }

    // Step 2: Preview & Edit (PRD mode only)
    if (step === 2) {
      return (
        <div>
          {decomposeSummary && (
            <Alert
              message={decomposeSummary}
              type="info"
              showIcon
              icon={<RobotOutlined />}
              style={{ marginBottom: 16 }}
            />
          )}
          <Table
            dataSource={decomposedTasks}
            columns={decomposeColumns}
            rowKey="key"
            pagination={false}
            size="small"
          />
          <Button
            type="dashed"
            icon={<PlusCircleOutlined />}
            onClick={addTask}
            style={{ width: '100%', marginTop: 8 }}
          >
            Add Task
          </Button>
        </div>
      );
    }

    return null;
  };

  // Determine wizard footer buttons
  const renderFooter = () => {
    const buttons: React.ReactNode[] = [];

    if (step > 0) {
      buttons.push(
        <Button key="back" onClick={() => setStep((s) => s - 1)}>
          Back
        </Button>
      );
    }

    if (step === 0) {
      buttons.push(
        <Button key="next" type="primary" onClick={() => setStep(1)}>
          Next
        </Button>
      );
    }

    if (step === 1 && createMode === 'single') {
      buttons.push(
        <Button key="create" type="primary" loading={creating} onClick={handleCreateSingle}>
          Create Task
        </Button>
      );
    }

    if (step === 1 && createMode === 'prd') {
      buttons.push(
        <Button
          key="analyze"
          type="primary"
          icon={<RobotOutlined />}
          loading={analyzing}
          onClick={handleDecompose}
        >
          {analyzing ? 'AI Analyzing...' : 'Analyze PRD'}
        </Button>
      );
    }

    if (step === 2) {
      buttons.push(
        <Button
          key="batch"
          type="primary"
          loading={creating}
          onClick={handleBatchCreate}
          disabled={decomposedTasks.length === 0}
        >
          Create {decomposedTasks.length} Tasks
        </Button>
      );
    }

    return buttons;
  };

  // Step titles
  const stepTitles = createMode === 'prd'
    ? ['Choose Mode', 'Input PRD', 'Preview & Edit']
    : ['Choose Mode', 'Task Details'];

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
          <Button key="create" type="primary" icon={<PlusOutlined />} onClick={handleOpenWizard}>
            New Task
          </Button>,
        ]}
        pagination={{ defaultPageSize: 20 }}
      />
      <Modal
        title={
          <Space>
            <span>Create Task</span>
            <span style={{ fontSize: 12, color: '#888' }}>
              Step {step + 1}/{stepTitles.length}: {stepTitles[step]}
            </span>
          </Space>
        }
        open={wizardOpen}
        onCancel={handleCloseWizard}
        footer={renderFooter()}
        width={step === 2 ? 900 : 640}
        destroyOnClose
      >
        <Spin spinning={analyzing} tip="AI is analyzing your requirements...">
          {renderStepContent()}
        </Spin>
      </Modal>
    </>
  );
};

export default TaskList;
