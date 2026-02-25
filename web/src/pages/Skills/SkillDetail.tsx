import React, { useEffect, useMemo, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  Card,
  Descriptions,
  Tag,
  Button,
  Spin,
  Space,
  Typography,
  Form,
  Input,
  Select,
  message,
  Popconfirm,
  Result,
  List,
} from 'antd';
import {
  ArrowLeftOutlined,
  EditOutlined,
  SaveOutlined,
  CloseOutlined,
  DeleteOutlined,
  RollbackOutlined,
} from '@ant-design/icons';
import {
  useArchiveSkill,
  useRollbackSkill,
  useSkill,
  useSkillVersions,
  useUpdateSkill,
} from '@/hooks/useSkills';
import { formatTimestamp } from '@/utils/formatters';
import { AGENT_ROLES } from '@/utils/constants';

const { Title } = Typography;
const { TextArea } = Input;

const LAYER_COLOR: Record<string, string> = {
  L1: 'blue',
  L2: 'green',
  L3: 'orange',
};

const STATUS_COLOR: Record<string, string> = {
  active: 'green',
  draft: 'orange',
  archived: 'default',
  deprecated: 'volcano',
};

const STATUS_LABEL: Record<string, string> = {
  active: '启用',
  draft: '草稿',
  archived: '归档',
  deprecated: '废弃',
};

const LAYER_OPTIONS = ['L1', 'L2', 'L3'];

const SkillDetail: React.FC = () => {
  const { name } = useParams<{ name: string }>();
  const navigate = useNavigate();
  const [form] = Form.useForm();
  const [editing, setEditing] = useState(false);
  const { data: skill, isLoading, isError, error, refetch } = useSkill(name!);
  const { data: versionData, isLoading: versionsLoading } = useSkillVersions(name!);
  const updateSkill = useUpdateSkill();
  const archiveSkill = useArchiveSkill();
  const rollbackSkill = useRollbackSkill();

  const roleLabelMap = useMemo(
    () => Object.fromEntries(AGENT_ROLES.map((role) => [role.key, role.name])),
    [],
  );

  useEffect(() => {
    if (!skill) return;
    form.setFieldsValue({
      display_name: skill.display_name,
      description: skill.description || '',
      layer: skill.layer,
      tags: skill.tags ?? [],
      applicable_roles: skill.applicable_roles ?? [],
      content: skill.content || '',
      git_path: skill.git_path || '',
    });
  }, [form, skill]);

  const resetFormToSkill = () => {
    if (!skill) return;
    form.setFieldsValue({
      display_name: skill.display_name,
      description: skill.description || '',
      layer: skill.layer,
      tags: skill.tags ?? [],
      applicable_roles: skill.applicable_roles ?? [],
      content: skill.content || '',
      git_path: skill.git_path || '',
    });
  };

  const handleSave = async () => {
    if (!skill) return;

    let values: {
      display_name: string;
      description?: string;
      layer?: string;
      tags?: string[];
      applicable_roles?: string[];
      content?: string;
      git_path?: string;
    };

    try {
      values = await form.validateFields();
    } catch {
      return;
    }

    try {
      await updateSkill.mutateAsync({
        name: skill.name,
        req: {
          display_name: values.display_name,
          description: values.description || undefined,
          layer: values.layer || undefined,
          tags: values.tags ?? [],
          applicable_roles: values.applicable_roles ?? [],
          content: values.content || null,
          git_path: values.git_path || null,
        },
      });
      message.success('Skill 更新成功');
      setEditing(false);
    } catch (err: any) {
      message.error(err?.response?.data?.detail || 'Skill 更新失败');
    }
  };

  const handleArchive = async () => {
    if (!skill) return;
    try {
      await archiveSkill.mutateAsync(skill.name);
      message.success('Skill 已归档');
      navigate('/skills');
    } catch (err: any) {
      message.error(err?.response?.data?.detail || 'Skill 归档失败');
    }
  };

  const handleRollback = async (version: string) => {
    if (!skill) return;
    try {
      await rollbackSkill.mutateAsync({ name: skill.name, version });
      message.success(`已回滚到版本 ${version}`);
      setEditing(false);
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '回滚失败');
    }
  };

  if (isLoading) {
    return <Spin size="large" style={{ display: 'block', margin: '100px auto' }} />;
  }

  if (isError) {
    const detail = (error as any)?.response?.data?.detail || (error as Error)?.message || '加载 Skill 详情失败';
    return (
      <Result
        status="error"
        title="无法加载 Skill 详情"
        subTitle={detail}
        extra={[
          <Button key="back" icon={<ArrowLeftOutlined />} onClick={() => navigate('/skills')}>
            返回 Skills 列表
          </Button>,
          <Button key="retry" type="primary" onClick={() => refetch()}>
            重试
          </Button>,
        ]}
      />
    );
  }

  if (!skill) {
    return (
      <Result
        status="404"
        title="Skill 不存在"
        extra={
          <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/skills')}>
            返回 Skills 列表
          </Button>
        }
      />
    );
  }

  const versions = versionData?.versions ?? [];

  return (
    <div>
      <Space style={{ marginBottom: 16 }}>
        <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/skills')}>
          返回列表
        </Button>
        {!editing ? (
          <>
            <Button icon={<EditOutlined />} onClick={() => setEditing(true)}>
              编辑
            </Button>
            <Popconfirm
              title="确认归档该 Skill？"
              description="归档后默认列表将不再展示该 Skill。"
              okText="确认归档"
              cancelText="取消"
              okButtonProps={{ danger: true, loading: archiveSkill.isPending }}
              onConfirm={handleArchive}
            >
              <Button danger icon={<DeleteOutlined />}>
                归档
              </Button>
            </Popconfirm>
          </>
        ) : (
          <>
            <Button
              type="primary"
              icon={<SaveOutlined />}
              onClick={handleSave}
              loading={updateSkill.isPending}
            >
              保存
            </Button>
            <Button
              icon={<CloseOutlined />}
              onClick={() => {
                resetFormToSkill();
                setEditing(false);
              }}
            >
              取消
            </Button>
          </>
        )}
      </Space>

      <Title level={4}>{skill.display_name}</Title>

      <Card title="Skill 详情" style={{ marginBottom: 16 }}>
        {editing ? (
          <Form form={form} layout="vertical">
            <Form.Item
              name="display_name"
              label="显示名称"
              rules={[{ required: true, message: '请填写显示名称' }]}
            >
              <Input />
            </Form.Item>
            <Form.Item name="description" label="描述">
              <TextArea autoSize={{ minRows: 2, maxRows: 6 }} />
            </Form.Item>
            <Form.Item
              name="layer"
              label="层级"
              rules={[{ required: true, message: '请选择层级' }]}
            >
              <Select
                options={LAYER_OPTIONS.map((layer) => ({
                  label: layer,
                  value: layer,
                }))}
              />
            </Form.Item>
            <Form.Item name="tags" label="标签">
              <Select mode="tags" tokenSeparators={[',']} />
            </Form.Item>
            <Form.Item name="applicable_roles" label="适用角色">
              <Select
                mode="multiple"
                options={AGENT_ROLES.map((role) => ({
                  label: role.name,
                  value: role.key,
                }))}
              />
            </Form.Item>
            <Form.Item name="content" label="内容">
              <TextArea autoSize={{ minRows: 8, maxRows: 20 }} />
            </Form.Item>
            <Form.Item name="git_path" label="Git 路径">
              <Input />
            </Form.Item>
          </Form>
        ) : (
          <Descriptions column={2}>
            <Descriptions.Item label="标识名">{skill.name}</Descriptions.Item>
            <Descriptions.Item label="层级">
              <Tag color={LAYER_COLOR[skill.layer] || 'default'}>{skill.layer}</Tag>
            </Descriptions.Item>
            <Descriptions.Item label="版本">{skill.version}</Descriptions.Item>
            <Descriptions.Item label="状态">
              <Tag color={STATUS_COLOR[skill.status] || 'default'}>
                {STATUS_LABEL[skill.status] || skill.status}
              </Tag>
            </Descriptions.Item>
            <Descriptions.Item label="标签" span={2}>
              {(skill.tags ?? []).length > 0 ? (skill.tags ?? []).map((tag) => <Tag key={tag}>{tag}</Tag>) : '-'}
            </Descriptions.Item>
            <Descriptions.Item label="适用角色" span={2}>
              {(skill.applicable_roles ?? []).length > 0
                ? (skill.applicable_roles ?? []).map((role) => <Tag key={role}>{roleLabelMap[role] || role}</Tag>)
                : '-'}
            </Descriptions.Item>
            <Descriptions.Item label="创建时间">{formatTimestamp(skill.created_at)}</Descriptions.Item>
            <Descriptions.Item label="更新时间">{formatTimestamp(skill.updated_at)}</Descriptions.Item>
          </Descriptions>
        )}
      </Card>

      {!editing && (
        <>
          <Card title="描述">
            <Typography.Paragraph>{skill.description || '-'}</Typography.Paragraph>
          </Card>

          <Card title="内容" style={{ marginTop: 16 }}>
            {skill.content ? (
              <pre style={{ fontSize: 12, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                {skill.content}
              </pre>
            ) : (
              <Typography.Text type="secondary">暂无内容</Typography.Text>
            )}
          </Card>

          <Card title="Git 路径" style={{ marginTop: 16 }}>
            <Typography.Text>{skill.git_path || '-'}</Typography.Text>
          </Card>

          <Card title="版本历史" style={{ marginTop: 16 }}>
            {versionsLoading ? (
              <Spin />
            ) : (
              <List
                dataSource={versions}
                locale={{ emptyText: '暂无版本记录' }}
                renderItem={(item) => (
                  <List.Item
                    actions={
                      item.current
                        ? [<Tag key={`${item.version}-current`}>当前版本</Tag>]
                        : [
                            <Popconfirm
                              key={`${item.version}-rollback`}
                              title={`确认回滚到 ${item.version}？`}
                              description="将使用该版本内容覆盖当前版本内容。"
                              okText="确认回滚"
                              cancelText="取消"
                              onConfirm={() => handleRollback(item.version)}
                            >
                              <Button
                                type="link"
                                icon={<RollbackOutlined />}
                                loading={rollbackSkill.isPending}
                              >
                                回滚
                              </Button>
                            </Popconfirm>,
                          ]
                    }
                  >
                    <List.Item.Meta
                      title={`${item.version} · ${formatTimestamp(item.created_at)}`}
                      description={item.change_summary || (item.current ? '当前生效版本' : '历史版本')}
                    />
                  </List.Item>
                )}
              />
            )}
          </Card>
        </>
      )}
    </div>
  );
};

export default SkillDetail;
