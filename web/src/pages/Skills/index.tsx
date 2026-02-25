import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Tag, Button, message, Space, Typography, Popconfirm } from 'antd';
import {
  DownloadOutlined,
  PlusOutlined,
  SyncOutlined,
  UploadOutlined,
} from '@ant-design/icons';
import {
  ModalForm,
  ProFormSelect,
  ProFormText,
  ProFormTextArea,
  ProTable,
} from '@ant-design/pro-components';
import { Link } from 'react-router-dom';
import type { ActionType, ProColumns } from '@ant-design/pro-components';
import { useQueryClient } from '@tanstack/react-query';
import {
  archiveSkill,
  createSkill as createSkillApi,
  getSkill,
  listSkills,
  updateSkill as updateSkillApi,
} from '@/services/skillApi';
import { useCreateSkill, useSkillStats, useSyncSkills } from '@/hooks/useSkills';
import type { Skill, SkillCreateRequest, SkillUpdateRequest } from '@/types/skill';
import { AGENT_ROLES } from '@/utils/constants';

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

const LAYER_OPTIONS = [
  { label: 'L1', value: 'L1' },
  { label: 'L2', value: 'L2' },
  { label: 'L3', value: 'L3' },
];

const LAYER_LEGACY_MAP: Record<string, 'L1' | 'L2' | 'L3'> = {
  foundation: 'L1',
  domain: 'L2',
  scenario: 'L3',
};

type SkillListQuery = {
  name?: string;
  layer?: string;
  tag?: string;
  role?: string;
  status?: string;
};

function parseStringArray(input: unknown): string[] {
  if (!Array.isArray(input)) return [];
  return input
    .map((item) => String(item).trim())
    .filter((item) => item.length > 0);
}

function normalizeImportLayer(rawLayer: unknown, tags: string[]): 'L1' | 'L2' | 'L3' {
  const layer = typeof rawLayer === 'string' ? rawLayer.trim() : '';
  if (layer === 'L1' || layer === 'L2' || layer === 'L3') {
    return layer;
  }

  const mapped = LAYER_LEGACY_MAP[layer];
  if (mapped) {
    if (!tags.includes(layer)) {
      tags.push(layer);
    }
    return mapped;
  }

  return 'L1';
}

function buildDownloadFile(content: string, filename: string) {
  const blob = new Blob([content], { type: 'application/json;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

const SkillList: React.FC = () => {
  const actionRef = useRef<ActionType>();
  const importInputRef = useRef<HTMLInputElement>(null);
  const currentFilterRef = useRef<SkillListQuery>({});
  const [createOpen, setCreateOpen] = useState(false);
  const [importing, setImporting] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [tagOptions, setTagOptions] = useState<Array<{ label: string; value: string }>>([]);
  const createSkillMutation = useCreateSkill();
  const syncSkillsMutation = useSyncSkills();
  const { data: skillStats, isLoading: statsLoading } = useSkillStats();
  const queryClient = useQueryClient();

  const roleLabelMap = useMemo(
    () => Object.fromEntries(AGENT_ROLES.map((role) => [role.key, role.name])),
    [],
  );

  useEffect(() => {
    let active = true;

    const loadAllTagOptions = async () => {
      try {
        const pageSize = 200;
        let page = 1;
        let total = 0;
        const tagSet = new Set<string>();

        while (true) {
          const res = await listSkills({ page, page_size: pageSize });
          total = res.total;

          for (const skill of res.items) {
            for (const tag of skill.tags ?? []) {
              if (tag?.trim()) {
                tagSet.add(tag.trim());
              }
            }
          }

          if (page * pageSize >= total || res.items.length === 0) {
            break;
          }
          page += 1;
        }

        if (!active) return;

        setTagOptions((prev) => {
          const next = Array.from(tagSet)
            .sort((a, b) => a.localeCompare(b))
            .map((tag) => ({ label: tag, value: tag }));
          if (
            prev.length === next.length &&
            prev.every((item, idx) => item.value === next[idx].value)
          ) {
            return prev;
          }
          return next;
        });
      } catch {
        // ignore option loading error, keep current options
      }
    };

    void loadAllTagOptions();

    return () => {
      active = false;
    };
  }, []);

  const columns: ProColumns<Skill>[] = [
    {
      title: '名称',
      dataIndex: 'name',
      order: 91,
      render: (_, record) => <Link to={`/skills/${record.name}`}>{record.display_name}</Link>,
    },
    {
      title: '层级',
      dataIndex: 'layer',
      valueType: 'select',
      fieldProps: {
        options: LAYER_OPTIONS,
      },
      order: 95,
      render: (_, record) => <Tag color={LAYER_COLOR[record.layer] || 'default'}>{record.layer}</Tag>,
    },
    {
      title: '标签',
      dataIndex: 'tags',
      search: false,
      render: (_, record) => {
        const tags = record.tags ?? [];
        if (!tags.length) return '-';
        return tags.map((tag) => <Tag key={tag}>{tag}</Tag>);
      },
    },
    {
      title: '适用角色',
      dataIndex: 'applicable_roles',
      search: false,
      render: (_, record) => {
        const roles = record.applicable_roles ?? [];
        if (!roles.length) return '-';
        return roles.map((role) => <Tag key={role}>{roleLabelMap[role] || role}</Tag>);
      },
    },
    {
      title: '状态',
      dataIndex: 'status',
      search: false,
      render: (_, record) => (
        <Tag color={STATUS_COLOR[record.status] || 'default'}>
          {STATUS_LABEL[record.status] || record.status}
        </Tag>
      ),
    },
    {
      title: '版本',
      dataIndex: 'version',
      search: false,
    },
    {
      title: '标签',
      dataIndex: 'tag',
      hideInTable: true,
      valueType: 'select',
      fieldProps: {
        options: tagOptions,
        showSearch: true,
      },
      order: 94,
    },
    {
      title: '角色',
      dataIndex: 'role',
      hideInTable: true,
      valueType: 'select',
      fieldProps: {
        options: AGENT_ROLES.map((role) => ({ label: role.name, value: role.key })),
      },
      order: 93,
    },
    {
      title: '状态',
      dataIndex: 'statusQuery',
      hideInTable: true,
      valueType: 'select',
      fieldProps: {
        options: [
          { label: '启用', value: 'active' },
          { label: '草稿', value: 'draft' },
          { label: '归档', value: 'archived' },
          { label: '废弃', value: 'deprecated' },
        ],
      },
      order: 92,
    },
  ];

  const layerSummary = `L1: ${skillStats?.by_layer?.L1 ?? 0} / L2: ${skillStats?.by_layer?.L2 ?? 0} / L3: ${skillStats?.by_layer?.L3 ?? 0}`;
  const statusSummary = `启用: ${skillStats?.by_status?.active ?? 0} / 草稿: ${skillStats?.by_status?.draft ?? 0} / 归档: ${skillStats?.by_status?.archived ?? 0}`;

  const handleSync = async () => {
    try {
      const result = await syncSkillsMutation.mutateAsync();
      message.success(`同步完成：扫描 ${result.synced}，新增 ${result.created}，更新 ${result.updated}`);
      actionRef.current?.reload();
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '同步失败');
    }
  };

  const handleExport = async () => {
    setExporting(true);
    try {
      const filters = currentFilterRef.current;
      const pageSize = 200;
      let page = 1;
      let total = 0;
      const items: Skill[] = [];

      while (true) {
        const res = await listSkills({
          page,
          page_size: pageSize,
          ...filters,
        });
        total = res.total;
        items.push(...res.items);
        if (items.length >= total || res.items.length === 0) break;
        page += 1;
      }

      const exportedAt = new Date();
      const payload = {
        exported_at: exportedAt.toISOString(),
        filters,
        total: items.length,
        items,
      };
      const ts = `${exportedAt.getFullYear()}${String(exportedAt.getMonth() + 1).padStart(2, '0')}${String(exportedAt.getDate()).padStart(2, '0')}-${String(exportedAt.getHours()).padStart(2, '0')}${String(exportedAt.getMinutes()).padStart(2, '0')}${String(exportedAt.getSeconds()).padStart(2, '0')}`;
      buildDownloadFile(JSON.stringify(payload, null, 2), `skills-export-${ts}.json`);
      message.success(`导出完成，共 ${items.length} 条`);
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '导出失败');
    } finally {
      setExporting(false);
    }
  };

  const handleImportFileChange = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;

    setImporting(true);
    try {
      const fileText = await file.text();
      const parsed = JSON.parse(fileText);
      const rawItems = Array.isArray(parsed) ? parsed : parsed?.items;
      if (!Array.isArray(rawItems) || rawItems.length === 0) {
        message.warning('导入文件中没有可用的 Skill 数据');
        return;
      }

      let created = 0;
      let updated = 0;
      let archived = 0;
      let failed = 0;
      const failureSamples: string[] = [];

      for (const raw of rawItems) {
        try {
          const name = String(raw?.name || '').trim();
          if (!name) {
            throw new Error('缺少 name');
          }

          const tags = parseStringArray(raw?.tags);
          const applicableRoles = parseStringArray(raw?.applicable_roles ?? raw?.applicableRoles);
          const layer = normalizeImportLayer(raw?.layer, tags);
          const displayName = String(raw?.display_name || raw?.displayName || name).trim();
          if (!displayName) {
            throw new Error('缺少 display_name');
          }

          const createReq: SkillCreateRequest = {
            name,
            display_name: displayName,
            description: typeof raw?.description === 'string' ? raw.description : undefined,
            layer,
            tags,
            applicable_roles: applicableRoles,
            content: typeof raw?.content === 'string' ? raw.content : undefined,
            git_path: typeof raw?.git_path === 'string' ? raw.git_path : undefined,
          };
          const updateReq: SkillUpdateRequest = {
            display_name: createReq.display_name,
            description: createReq.description,
            layer: createReq.layer,
            tags: createReq.tags ?? [],
            applicable_roles: createReq.applicable_roles ?? [],
            content: createReq.content ?? null,
            git_path: createReq.git_path ?? null,
          };

          const targetStatus = typeof raw?.status === 'string' ? raw.status : 'active';
          const needArchive = targetStatus === 'archived';

          let existing: Skill | null = null;
          try {
            existing = await getSkill(name);
          } catch (err: any) {
            if (err?.response?.status !== 404) {
              throw err;
            }
          }

          if (existing) {
            await updateSkillApi(name, updateReq);
            updated += 1;
          } else {
            await createSkillApi(createReq);
            created += 1;
          }

          if (needArchive) {
            await archiveSkill(name);
            archived += 1;
          }
        } catch (err: any) {
          failed += 1;
          if (failureSamples.length < 5) {
            failureSamples.push(err?.message || String(err));
          }
        }
      }

      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['skills'] }),
        queryClient.invalidateQueries({ queryKey: ['skillStats'] }),
      ]);
      actionRef.current?.reload();

      if (failed === 0) {
        message.success(`导入完成：新增 ${created}，更新 ${updated}，归档 ${archived}`);
      } else {
        message.warning(
          `导入部分完成：新增 ${created}，更新 ${updated}，归档 ${archived}，失败 ${failed}（示例：${failureSamples.join('；')}）`,
          8,
        );
      }
    } catch (err: any) {
      message.error(err?.message || '导入失败，请检查文件格式');
    } finally {
      setImporting(false);
    }
  };

  return (
    <>
      <input
        ref={importInputRef}
        type="file"
        accept=".json,application/json"
        style={{ display: 'none' }}
        onChange={handleImportFileChange}
      />

      <ProTable<Skill>
        headerTitle="Skills 管理"
        actionRef={actionRef}
        rowKey="name"
        columns={columns}
        search={{ labelWidth: 'auto', defaultCollapsed: false }}
        request={async (params) => {
          const filters: SkillListQuery = {
            name: typeof params.name === 'string' ? params.name.trim() || undefined : undefined,
            layer: typeof params.layer === 'string' ? params.layer : undefined,
            tag: typeof params.tag === 'string' ? params.tag : undefined,
            role: typeof params.role === 'string' ? params.role : undefined,
            status:
              typeof params.statusQuery === 'string' ? params.statusQuery : undefined,
          };
          currentFilterRef.current = filters;

          const res = await listSkills({
            page: params.current,
            page_size: params.pageSize,
            ...filters,
          });

          setTagOptions((prev) => {
            const tagSet = new Set<string>();
            for (const option of prev) {
              tagSet.add(option.value);
            }
            for (const skill of res.items) {
              for (const tag of skill.tags ?? []) {
                if (tag?.trim()) {
                  tagSet.add(tag.trim());
                }
              }
            }
            const next = Array.from(tagSet)
              .sort((a, b) => a.localeCompare(b))
              .map((tag) => ({ label: tag, value: tag }));
            if (
              prev.length === next.length &&
              prev.every((item, idx) => item.value === next[idx].value)
            ) {
              return prev;
            }
            return next;
          });

          return { data: res.items, total: res.total, success: true };
        }}
        pagination={{ defaultPageSize: 20, showSizeChanger: true }}
        tableExtraRender={() => (
          <Space size={24}>
            <Typography.Text>{statsLoading ? '-' : `分层统计：${layerSummary}`}</Typography.Text>
            <Typography.Text>{statsLoading ? '-' : `状态统计：${statusSummary}`}</Typography.Text>
          </Space>
        )}
        toolBarRender={() => [
          <Button
            key="import"
            icon={<UploadOutlined />}
            loading={importing}
            onClick={() => importInputRef.current?.click()}
          >
            导入
          </Button>,
          <Button key="export" icon={<DownloadOutlined />} loading={exporting} onClick={handleExport}>
            导出
          </Button>,
          <Popconfirm
            key="sync-confirm"
            title="确认执行同步？"
            description="将扫描 skills 目录并把变更同步到数据库。"
            onConfirm={handleSync}
            okText="确认"
            cancelText="取消"
          >
            <Button key="sync" icon={<SyncOutlined />} loading={syncSkillsMutation.isPending}>
              同步
            </Button>
          </Popconfirm>,
          <Button key="create" type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>
            新建 Skill
          </Button>,
        ]}
      />

      <ModalForm
        title="新建 Skill"
        open={createOpen}
        onOpenChange={setCreateOpen}
        submitter={{
          searchConfig: {
            submitText: '创建',
          },
          submitButtonProps: {
            loading: createSkillMutation.isPending,
          },
        }}
        onFinish={async (values) => {
          try {
            await createSkillMutation.mutateAsync({
              name: values.name,
              display_name: values.display_name,
              description: values.description || undefined,
              layer: values.layer || 'L1',
              tags: values.tags?.length ? values.tags : undefined,
              applicable_roles: values.applicable_roles?.length ? values.applicable_roles : undefined,
              content: values.content || undefined,
              git_path: values.git_path || undefined,
            });
            message.success('Skill 创建成功');
            actionRef.current?.reload();
            return true;
          } catch (err: any) {
            message.error(err?.response?.data?.detail || 'Skill 创建失败');
            return false;
          }
        }}
      >
        <ProFormText
          name="name"
          label="标识名"
          placeholder="例如：task-splitter"
          rules={[{ required: true, message: '请填写标识名' }]}
        />
        <ProFormText
          name="display_name"
          label="显示名称"
          placeholder="例如：任务拆分器"
          rules={[{ required: true, message: '请填写显示名称' }]}
        />
        <ProFormSelect
          name="layer"
          label="层级"
          initialValue="L1"
          options={LAYER_OPTIONS}
          rules={[{ required: true, message: '请选择层级' }]}
        />
        <ProFormSelect
          name="tags"
          label="标签"
          fieldProps={{ mode: 'tags', tokenSeparators: [','] }}
        />
        <ProFormSelect
          name="applicable_roles"
          label="适用角色"
          fieldProps={{ mode: 'multiple' }}
          options={AGENT_ROLES.map((role) => ({ label: role.name, value: role.key }))}
        />
        <ProFormTextArea name="description" label="描述" />
        <ProFormTextArea name="content" label="内容" />
        <ProFormText name="git_path" label="Git 路径" placeholder="例如：skills/L2-domain/xxx/SKILL.md" />
      </ModalForm>
    </>
  );
};

export default SkillList;
