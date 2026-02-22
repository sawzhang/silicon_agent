import React from 'react';
import { Card, Row, Col, Typography, message, Spin } from 'antd';
import { ProForm, ProFormText, ProFormDigit, ProFormSwitch, ProFormSelect } from '@ant-design/pro-components';
import { useAgentStore } from '@/stores/agentStore';
import { updateConfig, startAgent, stopAgent } from '@/services/agentApi';
import { AGENT_ROLES, ROLE_DISPLAY_NAMES } from '@/utils/constants';

const { Title } = Typography;

const ConfigPage: React.FC = () => {
  const agents = useAgentStore((s) => s.agents);

  const handleSave = async (role: string, values: Record<string, unknown>) => {
    try {
      await updateConfig(role, {
        model_name: values.model as string,
      });
      message.success(`${ROLE_DISPLAY_NAMES[role]} config updated`);
    } catch {
      message.error('Failed to update config');
    }
  };

  const handleToggle = async (role: string, start: boolean) => {
    try {
      if (start) {
        await startAgent(role);
        message.success(`${ROLE_DISPLAY_NAMES[role]} started`);
      } else {
        await stopAgent(role);
        message.success(`${ROLE_DISPLAY_NAMES[role]} stopped`);
      }
    } catch {
      message.error('Failed to toggle agent');
    }
  };

  return (
    <div>
      <Title level={4}>Agent Configuration</Title>
      <Row gutter={[16, 16]}>
        {AGENT_ROLES.map((role) => {
          const agent = agents[role.key];
          if (!agent) return null;
          return (
            <Col key={role.key} xs={24} md={12} lg={8}>
              <Card title={`${role.name} (${role.key})`} size="small">
                <ProForm
                  layout="vertical"
                  size="small"
                  initialValues={{
                    model: agent.model,
                    temperature: 0.7,
                    max_tokens: 4096,
                    enabled: agent.status !== 'stopped',
                  }}
                  onFinish={async (values) => {
                    await handleSave(role.key, values);
                    return true;
                  }}
                  submitter={{
                    searchConfig: { submitText: 'Save' },
                    resetButtonProps: false,
                  }}
                >
                  <ProFormSelect
                    name="model"
                    label="Model"
                    options={[
                      { label: 'Claude Sonnet 4', value: 'claude-sonnet-4-20250514' },
                      { label: 'Claude Opus 4', value: 'claude-opus-4-20250514' },
                      { label: 'Claude Haiku 3.5', value: 'claude-3-5-haiku-20241022' },
                    ]}
                  />
                  <ProFormDigit name="temperature" label="Temperature" min={0} max={2} fieldProps={{ step: 0.1 }} />
                  <ProFormDigit name="max_tokens" label="Max Tokens" min={256} max={16384} />
                  <ProFormSwitch
                    name="enabled"
                    label="Enabled"
                    fieldProps={{
                      onChange: (checked) => handleToggle(role.key, checked),
                    }}
                  />
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
