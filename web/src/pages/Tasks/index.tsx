import React, { useState, useRef } from 'react';
import {
  Button, Tag, message, Modal, Radio, Input, Table, Space, Popconfirm, Alert, Spin, Select,
} from 'antd';
import {
  PlusOutlined, DeleteOutlined, RobotOutlined, FormOutlined, PlusCircleOutlined, ReloadOutlined,
} from '@ant-design/icons';
import { ProTable } from '@ant-design/pro-components';
import type { ActionType, ProColumns } from '@ant-design/pro-components';
import { Link } from 'react-router-dom';
import { listTasks, decomposePrd, batchCreateTasks, createTask } from '@/services/taskApi';
import { useTemplateList } from '@/hooks/useTemplates';
import { useProjectList } from '@/hooks/useProjects';
import { useBatchRetryTasks } from '@/hooks/useTasks';

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
  const batchRetryTasks = useBatchRetryTasks();
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([]);
  const [selectedRows, setSelectedRows] = useState<Task[]>([]);


  // Wizard state
  const [wizardOpen, setWizardOpen] = useState(false);
  const [step, setStep] = useState(0);
  const [createMode, setCreateMode] = useState<CreateMode>('single');

  // Single mode state
  const [singleForm, setSingleForm] = useState({
    title: '', description: '', template_id: '', project_id: '', target_branch: '', yunxiao_task_id: 'silicon_agent',
  });

  // PRD mode state
  const [prdText, setPrdText] = useState('');
  const [prdProjectId, setPrdProjectId] = useState<string>('');
  const [prdTemplateId, setPrdTemplateId] = useState<string>('');
  const [prdTargetBranch, setPrdTargetBranch] = useState<string>('');
  const [prdYunxiaoTaskId, setPrdYunxiaoTaskId] = useState<string>('silicon_agent');
  const [decomposedTasks, setDecomposedTasks] = useState<EditableTask[]>([]);
  const [decomposeSummary, setDecomposeSummary] = useState('');
  const [analyzing, setAnalyzing] = useState(false);
  const [creating, setCreating] = useState(false);

  const resetWizard = () => {
    setStep(0);
    setCreateMode('single');
    setSingleForm({ title: '', description: '', template_id: '', project_id: '', target_branch: '', yunxiao_task_id: 'silicon_agent' });
    setPrdText('');
    setPrdProjectId('');
    setPrdTemplateId('');
    setPrdTargetBranch('');
    setPrdYunxiaoTaskId('silicon_agent');
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
    if (!singleForm.target_branch.trim()) {
      message.warning('Target Git Branch is required');
      return;
    }
    setCreating(true);
    try {
      await createTask({
        title: singleForm.title,
        description: singleForm.description,
        template_id: singleForm.template_id || undefined,
        project_id: singleForm.project_id || undefined,
        target_branch: singleForm.target_branch,
        yunxiao_task_id: 'silicon_agent',
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
    if (!prdTargetBranch.trim()) {
      message.warning('Target Git Branch is required');
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
          target_branch: prdTargetBranch,
          yunxiao_task_id: 'silicon_agent',
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

  const handleBatchRetry = async () => {
    if (selectedRows.length === 0) {
      message.warning('请先选择任务');
      return;
    }

    const failedTaskIds = selectedRows
      .filter((row) => row.status === 'failed')
      .map((row) => row.id);

    if (failedTaskIds.length === 0) {
      message.warning('所选任务中没有失败任务可重试');
      return;
    }

    try {
      const result = await batchRetryTasks.mutateAsync({ task_ids: failedTaskIds });
      const firstFailed = result.items.find((item) => !item.success);

      if (result.failed > 0) {
        message.warning(
          `批量重试已提交：成功 ${result.succeeded}，失败 ${result.failed}${firstFailed?.reason ? `（示例原因：${firstFailed.reason}）` : ''}`
        );
      } else {
        message.success(`批量重试已提交：成功 ${result.succeeded}，失败 ${result.failed}`);
      }

      setSelectedRowKeys([]);
      setSelectedRows([]);
      actionRef.current?.reload();
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '批量重试失败');
    }
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
    { title: '标题', dataIndex: 'title', ellipsis: true },
    {
      title: '云效 ID',
      dataIndex: 'yunxiao_task_id',
      width: 120,
      render: (_, record) => record.yunxiao_task_id ? <Tag color="blue">{record.yunxiao_task_id}</Tag> : '-',
    },
    {
      title: '模板',
      dataIndex: 'template_name',
      width: 100,
      search: false,
      render: (_, record) => record.template_name ? <Tag>{record.template_name}</Tag> : '-',
    },
    {
      title: '项目',
      dataIndex: 'project_name',
      width: 120,
      search: false,
      render: (_, record) => record.project_name || '-',
    },
    {
      title: '状态',
      dataIndex: 'status',
      valueEnum: { pending: '待处理', running: '运行中', completed: '已完成', failed: '失败', cancelled: '已取消' },
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
      title: '成本',
      dataIndex: 'total_cost_rmb',
      width: 100,
      search: false,
      render: (_, record) => formatCost(record.total_cost_rmb),
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      valueType: 'dateRange',
      render: (_, record) => formatTimestamp(record.created_at),
    },
  ];

  // Decomposed tasks table columns
  const decomposeColumns = [
    {
      title: '标题',
      dataIndex: 'title',
      render: (_: any, record: EditableTask) => (
        <Input
          value={record.title}
          onChange={(e) => updateTask(record.key, 'title', e.target.value)}
          placeholder="任务标题"
        />
      ),
    },
    {
      title: '描述',
      dataIndex: 'description',
      render: (_: any, record: EditableTask) => (
        <Input.TextArea
          value={record.description}
          onChange={(e) => updateTask(record.key, 'description', e.target.value)}
          autoSize={{ minRows: 2, maxRows: 6 }}
          placeholder="任务描述及验收标准"
        />
      ),
    },
    {
      title: '优先级',
      dataIndex: 'priority',
      width: 100,
      render: (_: any, record: EditableTask) => (
        <Tag color={PRIORITY_COLOR[record.priority]}>{record.priority}</Tag>
      ),
    },
    {
      title: '操作',
      width: 60,
      render: (_: any, record: EditableTask) => (
        <Popconfirm title="确认移除此任务？" onConfirm={() => removeTask(record.key)}>
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
              单个任务
            </Radio.Button>
            <Radio.Button value="prd" style={{ height: 80, lineHeight: '60px', padding: '0 32px' }}>
              <RobotOutlined style={{ marginRight: 8 }} />
              PRD 智能拆解
            </Radio.Button>
          </Radio.Group>
          <div style={{ marginTop: 16, color: '#888' }}>
            {createMode === 'single'
              ? '创建包含独立标题和描述的单个任务'
              : '粘贴 PRD 内容，让 AI 为你智能拆解为多个子任务并批量执行'}
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
              <label style={{ display: 'block', fontWeight: 500, marginBottom: 4 }}>标题 *</label>
              <Input
                value={singleForm.title}
                onChange={(e) => setSingleForm((p) => ({ ...p, title: e.target.value }))}
                placeholder="请输入任务标题"
              />
            </div>
            <div style={{ marginBottom: 16 }}>
              <label style={{ display: 'block', fontWeight: 500, marginBottom: 4 }}>描述 *</label>
              <Input.TextArea
                value={singleForm.description}
                onChange={(e) => setSingleForm((p) => ({ ...p, description: e.target.value }))}
                placeholder={'详细描述任务要求：\n- 需要实现什么功能\n- 验收标准\n- 技术约束'}
                autoSize={{ minRows: 6, maxRows: 12 }}
              />
            </div>
            <div style={{ marginBottom: 16 }}>
              <label style={{ display: 'block', fontWeight: 500, marginBottom: 4 }}>流水线模板</label>
              <select
                value={singleForm.template_id}
                onChange={(e) => setSingleForm((p) => ({ ...p, template_id: e.target.value }))}
                style={{ width: '100%', padding: '4px 8px', borderRadius: 6, border: '1px solid #d9d9d9' }}
              >
                <option value="">请选择模板</option>
                {templateOptions.map((o) => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
              {selectedTemplate && selectedTemplate.stages.length > 0 && (
                <div style={{ marginTop: 4, fontSize: 12, color: '#888' }}>
                  包含阶段: {selectedTemplate.stages.map((s: any) => STAGE_DISPLAY[s.name] || s.name).join(' → ')}
                </div>
              )}
            </div>
            <div style={{ marginBottom: 16 }}>
              <label style={{ display: 'block', fontWeight: 500, marginBottom: 4 }}>关联项目</label>
              <select
                value={singleForm.project_id}
                onChange={(e) => setSingleForm((p) => ({ ...p, project_id: e.target.value }))}
                style={{ width: '100%', padding: '4px 8px', borderRadius: 6, border: '1px solid #d9d9d9' }}
              >
                <option value="">请选择项目 (代码库上下文)</option>
                {projectOptions.map((o) => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
            </div>
            <div style={{ marginBottom: 16 }}>
              <label style={{ display: 'block', fontWeight: 500, marginBottom: 4 }}>云效 / 分支 *</label>
              <Input.Group compact style={{ display: 'flex' }}>
                <Input
                  style={{ width: '40%', backgroundColor: '#f5f5f5' }}
                  value="silicon_agent"
                  readOnly
                  disabled
                />
                <Input
                  style={{ width: '60%' }}
                  value={singleForm.target_branch}
                  onChange={(e) => setSingleForm((p) => ({ ...p, target_branch: e.target.value }))}
                  placeholder="目标 Git 分支（必填）"
                />
              </Input.Group>
              <div style={{ marginTop: 4, fontSize: 12, color: '#888' }}>
                代码将在评审通过后推送到该分支。
              </div>
            </div>
          </div>
        );
      }

      // PRD mode
      return (
        <div style={{ maxWidth: 700, margin: '0 auto' }}>
          <div style={{ marginBottom: 16 }}>
            <label style={{ display: 'block', fontWeight: 500, marginBottom: 4 }}>PRD 内容 *</label>
            <Input.TextArea
              value={prdText}
              onChange={(e) => setPrdText(e.target.value)}
              placeholder={'请将 PRD 文档粘贴到此处...\n\n例如：\n用户登录模块需求：\n1. 支持邮箱+密码登录\n2. 支持 OAuth2 第三方登录（Google, GitHub）\n3. 密码重置功能\n4. 登录日志审计'}
              autoSize={{ minRows: 10, maxRows: 20 }}
            />
          </div>
          <Space style={{ width: '100%' }} direction="vertical" size="middle">
            <div>
              <label style={{ display: 'block', fontWeight: 500, marginBottom: 4 }}>关联项目</label>
              <select
                value={prdProjectId}
                onChange={(e) => setPrdProjectId(e.target.value)}
                style={{ width: '100%', padding: '4px 8px', borderRadius: 6, border: '1px solid #d9d9d9' }}
              >
                <option value="">请选择项目</option>
                {projectOptions.map((o) => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
            </div>
            <div>
              <label style={{ display: 'block', fontWeight: 500, marginBottom: 4 }}>默认模板</label>
              <select
                value={prdTemplateId}
                onChange={(e) => setPrdTemplateId(e.target.value)}
                style={{ width: '100%', padding: '4px 8px', borderRadius: 6, border: '1px solid #d9d9d9' }}
              >
                <option value="">请选择模板</option>
                {templateOptions.map((o) => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
            </div>
            <div>
              <label style={{ display: 'block', fontWeight: 500, marginBottom: 4 }}>云效 / 分支 *</label>
              <Input.Group compact style={{ display: 'flex' }}>
                <Input
                  style={{ width: '40%', backgroundColor: '#f5f5f5' }}
                  value="silicon_agent"
                  readOnly
                  disabled
                />
                <Input
                  style={{ width: '60%' }}
                  value={prdTargetBranch}
                  onChange={(e) => setPrdTargetBranch(e.target.value)}
                  placeholder="目标 Git 分支（必填）"
                />
              </Input.Group>
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
            添加子任务
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
      buttons.push(<Button key="back" onClick={() => setStep((s) => s - 1)}>上一步</Button>);
    }

    if (step === 0) {
      buttons.push(<Button key="next" type="primary" onClick={() => setStep(1)}>下一步</Button>);
    }

    if (step === 1 && createMode === 'single') {
      buttons.push(
        <Button key="create" type="primary" loading={creating} onClick={handleCreateSingle}>
          创建任务
        </Button>
      );
    }

    if (step === 1 && createMode === 'prd') {
      buttons.push(
        <Button key="analyze" type="primary" icon={<RobotOutlined />} loading={analyzing} onClick={handleDecompose}>
          {analyzing ? 'AI 正在分析...' : '智能拆解 PRD'}
        </Button>
      );
    }

    if (step === 2) {
      buttons.push(
        <Button key="batch" type="primary" loading={creating} onClick={handleBatchCreate} disabled={decomposedTasks.length === 0}>
          创建 {decomposedTasks.length} 个任务
        </Button>
      );
    }

    return buttons;
  };

  const stepTitles = createMode === 'prd'
    ? ['选择模式', '输入 PRD', '预览与编辑']
    : ['选择模式', '任务详情'];

  return (
    <>
      <ProTable<Task>
        headerTitle="任务管线"
        actionRef={actionRef}
        rowKey="id"
        columns={columns}
        rowSelection={{
          selectedRowKeys,
          onChange: (keys, rows) => {
            setSelectedRowKeys(keys);
            setSelectedRows(rows);
          },
        }}
        request={async (params) => {
          const res = await listTasks({
            status: params.status,
            page: params.current,
            page_size: params.pageSize,
          });
          return { data: res.tasks, total: res.total, success: true };
        }}
        toolBarRender={() => [
          <Popconfirm
            key="retry-batch-confirm"
            title="确认批量重试选中的失败任务？"
            onConfirm={handleBatchRetry}
            okButtonProps={{ loading: batchRetryTasks.isPending }}
            disabled={selectedRowKeys.length === 0}
          >
            <Button
              key="retry-batch"
              icon={<ReloadOutlined />}
              loading={batchRetryTasks.isPending}
              disabled={selectedRowKeys.length === 0}
            >
              批量重试
            </Button>
          </Popconfirm>,
          <Button key="create" type="primary" icon={<PlusOutlined />} onClick={handleOpenWizard}>
            新建任务
          </Button>,
        ]}
        pagination={{ defaultPageSize: 20 }}
      />
      <Modal
        title={
          <Space>
            <span>创建任务</span>
            <span style={{ fontSize: 12, color: '#888' }}>
              第 {step + 1}/{stepTitles.length} 步：{stepTitles[step]}
            </span>
          </Space>
        }
        open={wizardOpen}
        onCancel={handleCloseWizard}
        footer={renderFooter()}
        width={step === 2 ? 900 : 640}
        destroyOnClose
      >
        <Spin spinning={analyzing} tip="AI 正在分析你的需求...">
          {renderStepContent()}
        </Spin>
      </Modal>
    </>
  );
};

export default TaskList;
