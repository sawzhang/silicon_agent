import React, { useMemo } from 'react';
import { Card, Col, Empty, Row, Spin, Typography, message } from 'antd';
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
import type { AgentConfigFormValues, AgentStatus } from '@/types/agent';

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

  if (agentsLoading || optionsLoading) {
    return <Spin size="large" />;
  }

  if (!agentsData?.agents.length) {
    return <Empty description="暂无 Agent 配置数据" />;
  }

  return (
    <div>
      <Title level={4}>Agent 配置中心</Title>
      <Paragraph type="secondary">
        配置项来自后端实时数据，可按角色独立调整模型、推理参数及扩展 Skill 目录。
      </Paragraph>
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
