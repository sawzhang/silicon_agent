import React, { useState } from 'react';
import { Tag, message, Button, Space, Popconfirm, Modal, Input, Switch } from 'antd';
import { PlusOutlined, DeleteOutlined, ExperimentOutlined } from '@ant-design/icons';
import { ProTable } from '@ant-design/pro-components';
import type { ProColumns } from '@ant-design/pro-components';
import {
  ModalForm,
  ProFormText,
  ProFormTextArea,
  ProFormSelect,
  ProFormDigit,
  ProFormSwitch,
} from '@ant-design/pro-components';
import { listProjectRules, createRule, updateRule, deleteRule, testRule } from '@/services/triggerApi';
import { formatTimestamp } from '@/utils/formatters';
import type { TriggerRule, TriggerTestResult } from '@/types/trigger';

const SOURCE_COLORS: Record<string, string> = {
  github: 'green',
  jira: 'blue',
  gitlab: 'orange',
  webhook: 'default',
  cron: 'purple',
};

interface Props {
  projectId: string;
}

const TriggerRulesTab: React.FC<Props> = ({ projectId }) => {
  const [createOpen, setCreateOpen] = useState(false);
  const [testModalOpen, setTestModalOpen] = useState(false);
  const [testingRule, setTestingRule] = useState<TriggerRule | null>(null);
  const [testPayload, setTestPayload] = useState('{}');
  const [testResult, setTestResult] = useState<TriggerTestResult | null>(null);
  const [testLoading, setTestLoading] = useState(false);
  const actionRef = React.useRef<any>();

  const handleTest = async () => {
    if (!testingRule) return;
    setTestLoading(true);
    try {
      const payload = JSON.parse(testPayload);
      const result = await testRule(testingRule.id, { payload });
      setTestResult(result);
    } catch (err: unknown) {
      const error = err as { response?: { data?: { detail?: string } }; message?: string };
      message.error(error?.response?.data?.detail || error?.message || '测试失败');
    } finally {
      setTestLoading(false);
    }
  };

  const handleToggleEnabled = async (rule: TriggerRule, enabled: boolean) => {
    try {
      await updateRule(rule.id, { enabled });
      message.success(enabled ? '已启用' : '已禁用');
      actionRef.current?.reload();
    } catch {
      message.error('操作失败');
    }
  };

  const columns: ProColumns<TriggerRule>[] = [
    { title: '名称', dataIndex: 'name', ellipsis: true },
    {
      title: '来源',
      dataIndex: 'source',
      width: 100,
      render: (_, r) => <Tag color={SOURCE_COLORS[r.source] || 'default'}>{r.source}</Tag>,
    },
    { title: '事件类型', dataIndex: 'event_type', width: 160 },
    {
      title: '启用',
      dataIndex: 'enabled',
      width: 80,
      render: (_, r) => (
        <Switch
          checked={r.enabled}
          size="small"
          onChange={(checked) => handleToggleEnabled(r, checked)}
        />
      ),
    },
    {
      title: '上次触发',
      dataIndex: 'last_triggered_at',
      width: 160,
      render: (_, r) => (r.last_triggered_at ? formatTimestamp(r.last_triggered_at) : '-'),
    },
    {
      title: '操作',
      width: 120,
      render: (_, r) => (
        <Space size="small">
          <Button
            type="link"
            size="small"
            icon={<ExperimentOutlined />}
            onClick={() => {
              setTestingRule(r);
              setTestResult(null);
              setTestPayload('{}');
              setTestModalOpen(true);
            }}
          >
            测试
          </Button>
          <Popconfirm
            title="确认删除此规则？"
            onConfirm={async () => {
              await deleteRule(r.id);
              message.success('规则已删除');
              actionRef.current?.reload();
            }}
          >
            <Button type="link" danger size="small" icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <>
      <ProTable<TriggerRule>
        actionRef={actionRef}
        rowKey="id"
        columns={columns}
        search={false}
        request={async () => {
          const rules = await listProjectRules(projectId);
          return { data: rules, total: rules.length, success: true };
        }}
        toolBarRender={() => [
          <Button
            key="create"
            type="primary"
            icon={<PlusOutlined />}
            onClick={() => setCreateOpen(true)}
          >
            新建规则
          </Button>,
        ]}
        pagination={false}
      />

      <ModalForm
        title="新建触发规则"
        open={createOpen}
        onOpenChange={setCreateOpen}
        onFinish={async (values: Record<string, any>) => {
          try {
            let filters = undefined;
            if (values.filters) {
              try {
                filters = JSON.parse(values.filters);
              } catch {
                message.error('过滤条件 JSON 格式无效');
                return false;
              }
            }
            await createRule({
              name: values.name,
              source: values.source,
              event_type: values.event_type,
              filters,
              template_id: values.template_id || undefined,
              title_template: values.title_template || undefined,
              desc_template: values.desc_template || undefined,
              dedup_key_template: values.dedup_key_template || undefined,
              dedup_window_hours: values.dedup_window_hours,
              enabled: values.enabled,
              project_id: projectId,
            });
            message.success('规则创建成功');
            actionRef.current?.reload();
            return true;
          } catch (err: unknown) {
            const error = err as { response?: { data?: { detail?: string } } };
            message.error(error?.response?.data?.detail || '创建失败');
            return false;
          }
        }}
      >
        <ProFormText name="name" label="规则名称" rules={[{ required: true }]} />
        <ProFormSelect
          name="source"
          label="事件来源"
          rules={[{ required: true }]}
          options={[
            { label: 'GitHub', value: 'github' },
            { label: 'Jira', value: 'jira' },
            { label: 'GitLab', value: 'gitlab' },
            { label: 'Webhook', value: 'webhook' },
            { label: 'Cron', value: 'cron' },
          ]}
        />
        <ProFormText
          name="event_type"
          label="事件类型"
          rules={[{ required: true }]}
          placeholder="如 pr_opened, issue_created, * (通配)"
        />
        <ProFormTextArea
          name="filters"
          label="过滤条件 (JSON)"
          placeholder='{"labels": ["auto-agent"], "branch": "main"}'
        />
        <ProFormText name="template_id" label="关联模板 ID" />
        <ProFormText
          name="title_template"
          label="任务标题模板"
          initialValue="自动任务: {event_type}"
        />
        <ProFormTextArea name="desc_template" label="任务描述模板" />
        <ProFormText name="dedup_key_template" label="去重键模板" placeholder="如 github:{pr_number}" />
        <ProFormDigit name="dedup_window_hours" label="去重时间窗口(小时)" initialValue={24} min={1} />
        <ProFormSwitch name="enabled" label="启用" initialValue={true} />
      </ModalForm>

      <Modal
        title={`测试规则: ${testingRule?.name || ''}`}
        open={testModalOpen}
        onCancel={() => setTestModalOpen(false)}
        onOk={handleTest}
        confirmLoading={testLoading}
        okText="执行测试"
        width={600}
      >
        <div style={{ marginBottom: 16 }}>
          <div style={{ marginBottom: 8 }}>模拟 Payload (JSON)：</div>
          <Input.TextArea
            rows={6}
            value={testPayload}
            onChange={(e) => setTestPayload(e.target.value)}
          />
        </div>
        {testResult && (
          <div>
            <Tag color={testResult.would_trigger ? 'success' : 'warning'}>{testResult.result}</Tag>
            <div style={{ marginTop: 8 }}>
              <div>过滤通过: {testResult.filter_passed ? '是' : '否'}</div>
              <div>去重拦截: {testResult.dedup_blocked ? '是' : '否'}</div>
              {testResult.rendered_title && <div>渲染标题: {testResult.rendered_title}</div>}
              {testResult.rendered_desc && <div>渲染描述: {testResult.rendered_desc}</div>}
            </div>
          </div>
        )}
      </Modal>
    </>
  );
};

export default TriggerRulesTab;
