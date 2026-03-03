import React, { useState } from 'react';
import {
  Card,
  Button,
  Switch,
  Input,
  Space,
  Popconfirm,
  message,
  Empty,
  Spin,
  Typography,
  Row,
  Col,
} from 'antd';
import {
  GithubOutlined,
  CopyOutlined,
  ReloadOutlined,
  DeleteOutlined,
  PlusOutlined,
} from '@ant-design/icons';
import {
  useIntegrationList,
  useCreateIntegration,
  useUpdateIntegration,
  useDeleteIntegration,
  useRegenerateSecret,
} from '@/hooks/useIntegrations';
import type { Integration } from '@/types/integration';

const { Text } = Typography;

const PROVIDER_INFO: Record<string, { label: string; icon: React.ReactNode }> = {
  github: { label: 'GitHub', icon: <GithubOutlined /> },
  jira: { label: 'Jira', icon: <Text strong>J</Text> },
  gitlab: { label: 'GitLab', icon: <Text strong>GL</Text> },
};

const ALL_PROVIDERS: Array<'github' | 'jira' | 'gitlab'> = ['github', 'jira', 'gitlab'];

interface Props {
  projectId: string;
}

const IntegrationCard: React.FC<{
  integration: Integration;
  projectId: string;
}> = ({ integration, projectId }) => {
  const [accessToken, setAccessToken] = useState('');
  const updateIntegration = useUpdateIntegration(projectId);
  const deleteIntegration = useDeleteIntegration(projectId);
  const regenerateSecret = useRegenerateSecret(projectId);
  const info = PROVIDER_INFO[integration.provider] || { label: integration.provider, icon: null };

  const baseUrl = window.location.origin;
  const webhookUrl = `${baseUrl}${integration.webhook_url}`;

  const handleCopy = (text: string) => {
    navigator.clipboard.writeText(text);
    message.success('已复制到剪贴板');
  };

  const handleToggleEnabled = async (enabled: boolean) => {
    await updateIntegration.mutateAsync({
      provider: integration.provider,
      req: { enabled },
    });
    message.success(enabled ? '已启用' : '已禁用');
  };

  const handleSaveToken = async () => {
    if (!accessToken) return;
    await updateIntegration.mutateAsync({
      provider: integration.provider,
      req: { access_token: accessToken },
    });
    setAccessToken('');
    message.success('Access Token 已更新');
  };

  return (
    <Card
      title={
        <Space>
          {info.icon}
          {info.label}
        </Space>
      }
      extra={
        <Switch
          checked={integration.enabled}
          onChange={handleToggleEnabled}
          loading={updateIntegration.isPending}
        />
      }
      style={{ marginBottom: 16 }}
    >
      <Space direction="vertical" style={{ width: '100%' }} size="middle">
        <div>
          <Text type="secondary">Webhook URL</Text>
          <Input
            value={webhookUrl}
            readOnly
            addonAfter={
              <CopyOutlined onClick={() => handleCopy(webhookUrl)} style={{ cursor: 'pointer' }} />
            }
          />
        </div>
        <div>
          <Text type="secondary">Webhook Secret</Text>
          <Input.Password
            value={integration.webhook_secret}
            readOnly
            addonAfter={
              <Space>
                <CopyOutlined
                  onClick={() => handleCopy(integration.webhook_secret)}
                  style={{ cursor: 'pointer' }}
                />
                <Popconfirm
                  title="重新生成 Webhook Secret？旧 Secret 将立即失效。"
                  onConfirm={async () => {
                    await regenerateSecret.mutateAsync(integration.provider);
                    message.success('Webhook Secret 已重新生成');
                  }}
                >
                  <ReloadOutlined style={{ cursor: 'pointer' }} />
                </Popconfirm>
              </Space>
            }
          />
        </div>
        <div>
          <Text type="secondary">Access Token</Text>
          <Space.Compact style={{ width: '100%' }}>
            <Input.Password
              value={accessToken}
              onChange={(e) => setAccessToken(e.target.value)}
              placeholder={integration.access_token || '未设置'}
            />
            <Button type="primary" onClick={handleSaveToken} disabled={!accessToken}>
              保存
            </Button>
          </Space.Compact>
        </div>
        <div style={{ textAlign: 'right' }}>
          <Popconfirm
            title={`确认删除 ${info.label} 集成配置？`}
            onConfirm={async () => {
              await deleteIntegration.mutateAsync(integration.provider);
              message.success('集成配置已删除');
            }}
          >
            <Button danger icon={<DeleteOutlined />} size="small">
              删除集成
            </Button>
          </Popconfirm>
        </div>
      </Space>
    </Card>
  );
};

const IntegrationConfigTab: React.FC<Props> = ({ projectId }) => {
  const { data: integrations, isLoading } = useIntegrationList(projectId);
  const createIntegration = useCreateIntegration(projectId);

  const configuredProviders = new Set(integrations?.map((i) => i.provider) || []);
  const unconfiguredProviders = ALL_PROVIDERS.filter((p) => !configuredProviders.has(p));

  if (isLoading) return <Spin />;

  return (
    <div>
      <Row gutter={16}>
        {(integrations || []).map((integration) => (
          <Col key={integration.provider} xs={24} lg={12} xl={8}>
            <IntegrationCard integration={integration} projectId={projectId} />
          </Col>
        ))}
      </Row>

      {unconfiguredProviders.length > 0 && (
        <>
          <Text type="secondary" style={{ display: 'block', marginBottom: 12 }}>
            可添加的集成
          </Text>
          <Row gutter={16}>
            {unconfiguredProviders.map((provider) => {
              const info = PROVIDER_INFO[provider];
              return (
                <Col key={provider} xs={24} lg={12} xl={8}>
                  <Card style={{ marginBottom: 16, textAlign: 'center' }}>
                    <Space direction="vertical">
                      <div style={{ fontSize: 24 }}>{info.icon}</div>
                      <Text>{info.label}</Text>
                      <Button
                        type="primary"
                        icon={<PlusOutlined />}
                        loading={createIntegration.isPending}
                        onClick={async () => {
                          await createIntegration.mutateAsync({ provider });
                          message.success(`${info.label} 集成已创建`);
                        }}
                      >
                        配置
                      </Button>
                    </Space>
                  </Card>
                </Col>
              );
            })}
          </Row>
        </>
      )}

      {!integrations?.length && !unconfiguredProviders.length && (
        <Empty description="暂无可配置的集成" />
      )}
    </div>
  );
};

export default IntegrationConfigTab;
