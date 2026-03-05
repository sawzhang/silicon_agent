import React, { useMemo, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Empty,
  Form,
  Input,
  InputNumber,
  Modal,
  Row,
  Space,
  Spin,
  Tag,
  Typography,
  message,
} from 'antd';
import {
  EditOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import {
  ProForm,
  ProFormDigit,
  ProFormSelect,
  ProFormSwitch,
  ProFormTextArea,
} from '@ant-design/pro-components';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AGENT_ROLES, ROLE_DISPLAY_NAMES } from '@/utils/constants';
import { getAgentConfigOptions, listAgents, startAgent, stopAgent, updateConfig } from '@/services/agentApi';
import { getLLMConfig, updateLLMConfig, probeLLM } from '@/services/llmProbeApi';
import type { AgentConfigFormValues, AgentStatus } from '@/types/agent';
import type { LLMConfigUpdateRequest } from '@/types/llmProbe';

const { Title, Paragraph } = Typography;

function buildInitialValues(agent: AgentStatus, fallbackModel?: string): AgentConfigFormValues {
  const config = agent.config ?? {};
  return {
    model_name: agent.model_name ?? fallbackModel,
    temperature: typeof config.temperature === 'number' ? config.temperature : 0.7,
    max_tokens: typeof config.max_tokens === 'number' ? config.max_tokens : 4096,
    max_turns: typeof config.max_turns === 'number' ? config.max_turns : 20,
    thinking_level: typeof config.thinking_level === 'string' ? config.thinking_level : 'medium',
    extra_skill_dirs: Array.isArray(config.extra_skill_dirs)
      ? config.extra_skill_dirs.filter((item): item is string => typeof item === 'string')
      : [],
    system_prompt_append:
      typeof config.system_prompt_append === 'string' ? config.system_prompt_append : '',
    enabled: agent.status !== 'idle' && agent.status !== 'stopped',
  };
}

const ConfigPage: React.FC = () => {
  const queryClient = useQueryClient();
  const [editModalOpen, setEditModalOpen] = useState(false);
  const [editForm] = Form.useForm<LLMConfigUpdateRequest>();

  const { data: llmConfig, isLoading: llmConfigLoading } = useQuery({
    queryKey: ['llm-config'],
    queryFn: getLLMConfig,
  });

  const llmUpdateMutation = useMutation({
    mutationFn: updateLLMConfig,
    onSuccess: () => {
      message.success('LLM 配置已更新');
      queryClient.invalidateQueries({ queryKey: ['llm-config'] });
      setEditModalOpen(false);
    },
    onError: () => {
      message.error('更新 LLM 配置失败');
    },
  });

  const probeMutation = useMutation({
    mutationFn: () => probeLLM({ timeout_ms: 5000 }),
  });

  const handleEditOpen = () => {
    editForm.setFieldsValue({
      base_url: llmConfig?.base_url,
      model: llmConfig?.model,
      timeout: llmConfig?.timeout,
    });
    setEditModalOpen(true);
  };

  const handleEditSubmit = async () => {
    const values = await editForm.validateFields();
    const payload: LLMConfigUpdateRequest = {};
    if (values.api_key) payload.api_key = values.api_key;
    if (values.base_url !== undefined) payload.base_url = values.base_url;
    if (values.model !== undefined) payload.model = values.model;
    if (values.timeout !== undefined) payload.timeout = values.timeout;
    await llmUpdateMutation.mutateAsync(payload);
  };

  const { data: agentsData, isLoading: agentsLoading } = useQuery({
    queryKey: ['agents'],
    queryFn: listAgents,
    refetchInterval: 15_000,
  });

  const { data: optionsData, isLoading: optionsLoading } = useQuery({
    queryKey: ['agent-config-options'],
    queryFn: getAgentConfigOptions,
  });

  const refreshAgents = async () => {
    await queryClient.invalidateQueries({ queryKey: ['agents'] });
  };

  const updateMutation = useMutation({
    mutationFn: ({ role, payload }: { role: string; payload: AgentConfigFormValues }) =>
      updateConfig(role, payload),
    onSuccess: async (_, variables) => {
      message.success(`${ROLE_DISPLAY_NAMES[variables.role]} 配置已更新`);
      await refreshAgents();
    },
    onError: () => {
      message.error('更新配置失败');
    },
  });

  const toggleMutation = useMutation({
    mutationFn: ({ role, enabled }: { role: string; enabled: boolean }) =>
      enabled ? startAgent(role) : stopAgent(role),
    onSuccess: async (_, variables) => {
      message.success(`${ROLE_DISPLAY_NAMES[variables.role]} 已${variables.enabled ? '启动' : '停止'}`);
      await refreshAgents();
    },
    onError: () => {
      message.error('切换运行状态失败');
    },
  });

  const agentsByRole = useMemo(() => {
    const map = new Map<string, AgentStatus>();
    for (const agent of agentsData?.agents ?? []) {
      map.set(agent.role, agent);
    }
    return map;
  }, [agentsData]);

  const modelOptions = (optionsData?.available_models ?? []).map((model) => ({
    label: model,
    value: model,
  }));

  const thinkingLevelOptions = (optionsData?.thinking_levels ?? []).map((item) => ({
    label: item,
    value: item,
  }));

  if (agentsLoading || optionsLoading || llmConfigLoading) {
    return <Spin size="large" />;
  }

  if (!agentsData?.agents.length) {
    return <Empty description="暂无 Agent 配置数据" />;
  }

  const roleModelEntries = Object.entries(llmConfig?.role_model_map ?? {});

  return (
    <div>
      <Title level={4}>Agent 配置中心</Title>
      <Paragraph type="secondary">
        配置项来自后端实时数据，可按角色独立调整模型、推理参数及扩展 Skill 目录。
      </Paragraph>

      {/* LLM 连接配置 */}
      <Card
        title="LLM 连接配置"
        size="small"
        style={{ marginBottom: 16 }}
        extra={
          <Space>
            <Button
              icon={<ThunderboltOutlined />}
              size="small"
              loading={probeMutation.isPending}
              onClick={() => probeMutation.mutate()}
            >
              探活
            </Button>
            <Button icon={<EditOutlined />} size="small" type="primary" onClick={handleEditOpen}>
              编辑
            </Button>
          </Space>
        }
      >
        <Descriptions size="small" column={{ xs: 1, sm: 2, lg: 4 }} bordered>
          <Descriptions.Item label="Base URL">{llmConfig?.base_url || '-'}</Descriptions.Item>
          <Descriptions.Item label="默认模型">{llmConfig?.model || '-'}</Descriptions.Item>
          <Descriptions.Item label="API Key">
            {llmConfig?.api_key_set ? (
              <Tag color="green">{llmConfig.api_key_masked}</Tag>
            ) : (
              <Tag color="red">未设置</Tag>
            )}
          </Descriptions.Item>
          <Descriptions.Item label="超时(秒)">{llmConfig?.timeout ?? '-'}</Descriptions.Item>
        </Descriptions>

        {roleModelEntries.length > 0 && (
          <div style={{ marginTop: 12 }}>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              角色模型映射:
            </Typography.Text>
            <div style={{ marginTop: 4 }}>
              {roleModelEntries.map(([role, model]) => (
                <Tag key={role} style={{ marginBottom: 4 }}>
                  {ROLE_DISPLAY_NAMES[role] || role}: {model}
                </Tag>
              ))}
            </div>
          </div>
        )}

        {probeMutation.data && (
          <div style={{ marginTop: 12 }}>
            <Space>
              <Tag color={probeMutation.data.ok ? 'success' : 'error'}>
                {probeMutation.data.ok ? '连接正常' : '连接异常'}
              </Tag>
              <Tag>延迟: {probeMutation.data.latency_ms}ms</Tag>
              <Tag>模型: {probeMutation.data.resolved_model || probeMutation.data.requested_model || '-'}</Tag>
            </Space>
            {!probeMutation.data.ok && probeMutation.data.error_message && (
              <Alert type="error" showIcon style={{ marginTop: 8 }} message={probeMutation.data.error_message} />
            )}
          </div>
        )}

        {probeMutation.error && (
          <Alert
            type="error"
            showIcon
            style={{ marginTop: 12 }}
            message={(probeMutation.error as any)?.response?.data?.detail || '探活请求失败'}
          />
        )}
      </Card>

      <Modal
        title="编辑 LLM 连接配置"
        open={editModalOpen}
        onCancel={() => setEditModalOpen(false)}
        onOk={handleEditSubmit}
        confirmLoading={llmUpdateMutation.isPending}
        okText="保存"
        destroyOnClose
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          message="修改仅在当前运行期间生效，重启后恢复 .env 配置。"
        />
        <Form form={editForm} layout="vertical">
          <Form.Item label="API Key" name="api_key" help="留空表示不修改">
            <Input.Password placeholder="sk-..." />
          </Form.Item>
          <Form.Item label="Base URL" name="base_url" rules={[{ required: true, message: '请输入 Base URL' }]}>
            <Input placeholder="https://api.openai.com/v1" />
          </Form.Item>
          <Form.Item label="默认模型" name="model" rules={[{ required: true, message: '请输入模型名称' }]}>
            <Input placeholder="gpt-4o" />
          </Form.Item>
          <Form.Item label="超时(秒)" name="timeout" rules={[{ required: true, message: '请输入超时时间' }]}>
            <InputNumber min={1} max={300} style={{ width: '100%' }} />
          </Form.Item>
        </Form>
      </Modal>

      <Row gutter={[16, 16]}>
        {AGENT_ROLES.map((roleMeta) => {
          const role = roleMeta.key;
          const agent = agentsByRole.get(role);
          if (!agent) {
            return null;
          }

          const defaultModel = optionsData?.role_defaults?.[role];
          const initialValues = buildInitialValues(agent, defaultModel);

          return (
            <Col key={role} xs={24} xl={12}>
              <Card title={`${roleMeta.name} (${role})`} size="small">
                <ProForm<AgentConfigFormValues>
                  key={`${role}-${agent.status}-${agent.updated_at}-${agent.started_at ?? ''}`}
                  layout="vertical"
                  submitter={{ searchConfig: { submitText: '保存配置' }, resetButtonProps: false }}
                  initialValues={initialValues}
                  onFinish={async (values) => {
                    await updateMutation.mutateAsync({ role, payload: values });
                    const enabled = Boolean(values.enabled);
                    if (enabled !== initialValues.enabled) {
                      await toggleMutation.mutateAsync({ role, enabled });
                    }
                    return true;
                  }}
                >
                  <ProFormSelect
                    name="model_name"
                    label="模型"
                    options={modelOptions}
                    rules={[{ required: true, message: '请选择模型' }]}
                  />
                  <Row gutter={12}>
                    <Col span={8}>
                      <ProFormDigit name="temperature" label="Temperature" min={0} max={2} fieldProps={{ step: 0.1 }} />
                    </Col>
                    <Col span={8}>
                      <ProFormDigit name="max_tokens" label="Max Tokens" min={256} max={32768} />
                    </Col>
                    <Col span={8}>
                      <ProFormDigit name="max_turns" label="Max Turns" min={1} max={200} />
                    </Col>
                  </Row>
                  <ProFormSelect name="thinking_level" label="Thinking Level" options={thinkingLevelOptions} />
                  <ProFormSelect name="extra_skill_dirs" label="额外 Skill 目录" mode="tags" />
                  <ProFormTextArea name="system_prompt_append" label="系统提示词追加" fieldProps={{ rows: 3, maxLength: 1000 }} />
                  <ProFormSwitch name="enabled" label="运行状态" />
                </ProForm>
              </Card>
            </Col>
          );
        })}
      </Row>
    </div>
  );
};

export default ConfigPage;
