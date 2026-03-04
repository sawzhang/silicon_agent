import React, { useState } from 'react';
import {
  Button,
  Card,
  Form,
  Input,
  InputNumber,
  Select,
  Space,
  Tag,
  Alert,
  message,
} from 'antd';
import { SendOutlined, EyeOutlined } from '@ant-design/icons';
import { Link } from 'react-router-dom';
import { mockWebhook } from '@/services/triggerApi';
import type { MockWebhookRequest, MockWebhookResponse } from '@/types/trigger';

const SOURCE_OPTIONS = [
  { label: 'GitHub', value: 'github' },
  { label: 'GitLab', value: 'gitlab' },
  { label: 'Jira', value: 'jira' },
  { label: 'Webhook', value: 'webhook' },
];

const EVENT_TYPE_OPTIONS: Record<string, { label: string; value: string }[]> = {
  github: [
    { label: 'Issue 创建', value: 'issues.opened' },
    { label: 'Issue 关闭', value: 'issues.closed' },
    { label: 'PR 创建', value: 'pull_request.opened' },
    { label: 'PR 合并', value: 'pull_request.closed' },
    { label: 'Push', value: 'push' },
  ],
  gitlab: [
    { label: 'Issue 创建', value: 'issue.open' },
    { label: 'MR 创建', value: 'merge_request.open' },
    { label: 'Push', value: 'push' },
  ],
  jira: [
    { label: 'Issue 创建', value: 'jira:issue_created' },
    { label: 'Issue 更新', value: 'jira:issue_updated' },
  ],
  webhook: [
    { label: '自定义事件', value: 'custom' },
  ],
};

const RESULT_CONFIG: Record<string, { color: string; label: string }> = {
  triggered: { color: 'success', label: '已触发' },
  would_trigger: { color: 'blue', label: '将会触发' },
  skipped_no_rule: { color: 'default', label: '无匹配规则' },
  skipped_filter: { color: 'warning', label: '过滤跳过' },
  skipped_dedup: { color: 'processing', label: '去重跳过' },
};

interface Props {
  projectId: string;
}

const MockWebhookTab: React.FC<Props> = ({ projectId }) => {
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<MockWebhookResponse | null>(null);
  const [selectedSource, setSelectedSource] = useState('github');

  const handleSubmit = async (dryRun: boolean) => {
    try {
      const values = await form.validateFields();
      setLoading(true);
      setResult(null);

      const labels = values.labels
        ? values.labels.split(',').map((s: string) => s.trim()).filter(Boolean)
        : undefined;

      const req: MockWebhookRequest = {
        source: values.source,
        event_type: values.event_type,
        title: values.title,
        body: values.body || undefined,
        number: values.number ?? undefined,
        author: values.author || undefined,
        ref: values.ref || undefined,
        labels,
        dry_run: dryRun,
      };

      const resp = await mockWebhook(projectId, req);
      setResult(resp);

      if (!dryRun && resp.matched) {
        message.success('任务已创建');
      } else if (!dryRun && !resp.matched) {
        message.warning('无匹配规则，未创建任务');
      }
    } catch (err: unknown) {
      if (err && typeof err === 'object' && 'errorFields' in err) return;
      message.error('请求失败');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ maxWidth: 720 }}>
      <Alert
        message="模拟触发"
        description="模拟外部 webhook 事件，跳过 HMAC 签名验证，直接触发项目的触发规则。适用于本地开发调试。"
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
      />

      <Card>
        <Form
          form={form}
          layout="vertical"
          initialValues={{ source: 'github', event_type: 'issues.opened' }}
        >
          <Form.Item label="事件来源" name="source" rules={[{ required: true }]}>
            <Select
              options={SOURCE_OPTIONS}
              onChange={(v: string) => {
                setSelectedSource(v);
                const events = EVENT_TYPE_OPTIONS[v];
                if (events?.length) {
                  form.setFieldsValue({ event_type: events[0].value });
                }
              }}
            />
          </Form.Item>

          <Form.Item label="事件类型" name="event_type" rules={[{ required: true }]}>
            <Select options={EVENT_TYPE_OPTIONS[selectedSource] || []} />
          </Form.Item>

          <Form.Item label="标题" name="title" rules={[{ required: true, message: '请输入标题' }]}>
            <Input placeholder="Issue / PR / 提交标题" />
          </Form.Item>

          <Form.Item label="正文" name="body">
            <Input.TextArea rows={3} placeholder="Issue / PR body（可选）" />
          </Form.Item>

          <Space style={{ width: '100%' }} size="middle">
            <Form.Item label="编号" name="number" style={{ width: 140 }}>
              <InputNumber min={1} placeholder="#N" style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item label="触发人" name="author" style={{ width: 180 }}>
              <Input placeholder="用户名" />
            </Form.Item>
            <Form.Item label="分支" name="ref" style={{ width: 200 }}>
              <Input placeholder="refs/heads/main" />
            </Form.Item>
          </Space>

          <Form.Item label="标签" name="labels">
            <Input placeholder="逗号分隔，如: bug,urgent" />
          </Form.Item>

          <Form.Item>
            <Space>
              <Button
                icon={<EyeOutlined />}
                onClick={() => handleSubmit(true)}
                loading={loading}
              >
                预览 (Dry Run)
              </Button>
              <Button
                type="primary"
                icon={<SendOutlined />}
                onClick={() => handleSubmit(false)}
                loading={loading}
              >
                触发
              </Button>
            </Space>
          </Form.Item>
        </Form>
      </Card>

      {result && (
        <Card style={{ marginTop: 16 }} title="结果">
          <Space direction="vertical" size="small" style={{ width: '100%' }}>
            <div>
              <strong>模式：</strong>
              {result.dry_run ? <Tag>预览</Tag> : <Tag color="blue">实际触发</Tag>}
            </div>
            <div>
              <strong>结果：</strong>
              <Tag color={RESULT_CONFIG[result.result]?.color || 'default'}>
                {RESULT_CONFIG[result.result]?.label || result.result}
              </Tag>
            </div>
            {result.task_id && (
              <div>
                <strong>任务：</strong>
                <Link to={`/tasks/${result.task_id}`}>{result.task_id}</Link>
              </div>
            )}
            {result.rendered_title && (
              <div>
                <strong>渲染标题：</strong> {result.rendered_title}
              </div>
            )}
            {result.rendered_desc && (
              <div>
                <strong>渲染描述：</strong> {result.rendered_desc}
              </div>
            )}
            {result.matched_rule && (
              <div>
                <strong>匹配规则：</strong> {result.matched_rule.name} ({result.matched_rule.id})
              </div>
            )}
          </Space>
        </Card>
      )}
    </div>
  );
};

export default MockWebhookTab;
