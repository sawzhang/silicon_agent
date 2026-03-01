import React, { useCallback, useEffect, useState, useRef } from 'react';
import { Alert, Button, Drawer, Space, Tag, Typography, Tabs, Descriptions, Select } from 'antd';
import { SyncOutlined } from '@ant-design/icons';
import { ProTable, type ActionType, type ProColumns } from '@ant-design/pro-components';
import { listTaskLogs, type TaskLogEvent } from '@/services/taskLogApi';
import { getTaskStages, listTasks } from '@/services/taskApi';
import { listProjects } from '@/services/projectApi';
import { useTaskLogStreamStore } from '@/stores/taskLogStreamStore';
import { formatTimestamp } from '@/utils/formatters';

const { Text } = Typography;

const STATUS_COLOR: Record<string, string> = {
  sent: 'processing',
  running: 'processing',
  success: 'green',
  failed: 'red',
  cancelled: 'orange',
};

const SOURCE_COLOR: Record<string, string> = {
  llm: 'blue',
  tool: 'purple',
  system: 'gold',
};

const TERMINAL_STREAM_STATUS = new Set(['success', 'failed', 'cancelled']);

// 辅助组件：带复制功能的代码/JSON展示块
const CodeBlock: React.FC<{ content: string; maxHeight?: number }> = ({ content, maxHeight = 400 }) => (
  <div style={{ position: 'relative', border: '1px solid #f0f0f0', borderRadius: 6, background: '#fafafa' }}>
    <div style={{ position: 'absolute', top: 8, right: 8, zIndex: 10 }}>
      <Typography.Text copyable={{ text: content }} />
    </div>
    <pre style={{ margin: 0, padding: '12px 36px 12px 12px', maxHeight, overflow: 'auto', whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontSize: 13 }}>
      {content}
    </pre>
  </div>
);

import type { ProFormInstance } from '@ant-design/pro-components';

const TaskLogsPage: React.FC = () => {
  const actionRef = useRef<ActionType>();
  const formRef = useRef<ProFormInstance>();
  const [autoRefreshInterval, setAutoRefreshInterval] = useState<number>(0);

  // Filter state for dynamic stage options
  const [selectedTask, setSelectedTask] = useState<string | undefined>();
  const [stageOptions, setStageOptions] = useState<{label: string, value: string}[]>([]);
  const [stageLoading, setStageLoading] = useState(false);

  // Streaming State
  const [streamingLog, setStreamingLog] = useState<TaskLogEvent | null>(null);
  const streamPreRef = useRef<HTMLPreElement>(null);
  
  const linesByLog = useTaskLogStreamStore((s) => s.linesByLog);
  const statusByLog = useTaskLogStreamStore((s) => s.statusByLog);
  const subscribeStream = useTaskLogStreamStore((s) => s.subscribe);
  const unsubscribeStream = useTaskLogStreamStore((s) => s.unsubscribe);
  const clearStream = useTaskLogStreamStore((s) => s.clear);
  const setStreamStatus = useTaskLogStreamStore((s) => s.setStatus);

  // Auto-refresh timer
  useEffect(() => {
    if (autoRefreshInterval === 0) return;
    const timer = setInterval(() => {
      actionRef.current?.reload();
    }, autoRefreshInterval);
    return () => clearInterval(timer);
  }, [autoRefreshInterval]);

  // Load dynamic stage options when task selection changes
  useEffect(() => {
    if (!selectedTask) {
      setStageOptions([]);
      return;
    }
    let canceled = false;
    const loadStages = async () => {
      setStageLoading(true);
      try {
        const stages = await getTaskStages(selectedTask);
        if (canceled) return;
        const uniqueStageNames = Array.from(new Set(stages.map((item) => item.stage_name).filter(Boolean)));
        setStageOptions(uniqueStageNames.map((name) => ({ label: name, value: name })));
      } catch {
        if (!canceled) setStageOptions([]);
      } finally {
        if (!canceled) setStageLoading(false);
      }
    };
    const timer = window.setTimeout(loadStages, 300);
    return () => {
      canceled = true;
      window.clearTimeout(timer);
    };
  }, [selectedTask]);

  // Streaming Side Effects
  useEffect(() => {
    if (!streamingLog) return;
    return () => {
      unsubscribeStream(streamingLog.id);
    };
  }, [streamingLog, unsubscribeStream]);

  const streamLines = streamingLog ? linesByLog[streamingLog.id] || [] : [];
  const streamStatus = streamingLog
    ? statusByLog[streamingLog.id] || streamingLog.status
    : undefined;

  useEffect(() => {
    if (streamPreRef.current) {
      streamPreRef.current.scrollTop = streamPreRef.current.scrollHeight;
    }
  }, [streamLines]);

  useEffect(() => {
    if (!streamingLog || !streamStatus) return;
    if (!TERMINAL_STREAM_STATUS.has(streamStatus)) return;
    actionRef.current?.reload();
  }, [streamStatus, streamingLog]);

  useEffect(() => {
    if (!streamingLog) return;
    if (TERMINAL_STREAM_STATUS.has(streamStatus || '')) return;
    const timer = window.setInterval(() => {
      actionRef.current?.reload();
    }, 3000);
    return () => window.clearInterval(timer);
  }, [streamStatus, streamingLog]);

  const openStream = useCallback(
    (record: TaskLogEvent) => {
      if (streamingLog && streamingLog.id !== record.id) {
        unsubscribeStream(streamingLog.id);
      }
      clearStream(record.id);
      subscribeStream(record.id);
      setStreamingLog(record);
    },
    [clearStream, streamingLog, subscribeStream, unsubscribeStream],
  );

  const closeStream = useCallback(() => {
    if (streamingLog) {
      unsubscribeStream(streamingLog.id);
    }
    setStreamingLog(null);
  }, [streamingLog, unsubscribeStream]);

  const columns: ProColumns<TaskLogEvent>[] = [
    {
      title: '项目',
      dataIndex: 'project',
      hideInTable: true,
      valueType: 'select',
      request: async ({ keyWords }) => {
        const res = await listProjects({ page: 1, page_size: 20, name: keyWords });
        return res.items.map(item => ({ label: item.display_name, value: item.id }));
      },
      fieldProps: {
        showSearch: true,
        onChange: () => {
          formRef.current?.setFieldValue('task', undefined);
          formRef.current?.setFieldValue('stage', undefined);
          setSelectedTask(undefined);
          setStageOptions([]);
        }
      },
      order: 4,
    },
    {
      title: '任务',
      dataIndex: 'task',
      hideInTable: true,
      valueType: 'select',
      dependencies: ['project'],
      formItemProps: {
        rules: [{ required: true, message: '请选择任务' }],
      },
      request: async (params) => {
        const res = await listTasks({ page: 1, page_size: 20, project_id: params.project, title: params.keyWords });
        return res.tasks.map(item => ({ label: `${item.title} (${item.id.slice(0, 8)})`, value: item.id }));
      },
      fieldProps: {
        showSearch: true,
        onChange: (val: string) => {
          setSelectedTask(val);
          formRef.current?.setFieldValue('stage', undefined);
        },
      },
      order: 3,
    },
    {
      title: '阶段',
      dataIndex: 'stage',
      hideInTable: true,
      valueType: 'select',
      dependencies: ['task'],
      fieldProps: {
        options: stageOptions,
        loading: stageLoading,
        showSearch: true,
      },
      order: 2,
    },
    {
      title: '来源',
      dataIndex: 'event_source',
      hideInTable: true,
      valueType: 'select',
      valueEnum: {
        'llm': { text: 'LLM', status: 'Processing' },
        'tool': { text: 'Tool', status: 'Default' },
        'system': { text: '系统', status: 'Warning' },
      },
      order: 1,
    },
    {
      title: '序号',
      dataIndex: 'event_seq',
      width: 70,
      align: 'center',
      search: false,
    },
    {
      title: '时间',
      dataIndex: 'created_at',
      width: 170,
      search: false,
      render: (_, record) => formatTimestamp(record.created_at),
    },
    {
      title: '阶段',
      dataIndex: 'stage_name',
      width: 140,
      ellipsis: true,
      search: false,
      render: (_, record) => record.stage_name || '-',
    },
    {
      title: '来源',
      dataIndex: 'event_source',
      width: 100,
      align: 'center',
      search: false,
      render: (_, record) => (
        <Tag color={SOURCE_COLOR[record.event_source] || 'default'} style={{ margin: 0 }}>
          {record.event_source.toUpperCase()}
        </Tag>
      ),
    },
    {
      title: '类型',
      dataIndex: 'event_type',
      width: 180,
      ellipsis: true,
      search: false,
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 90,
      align: 'center',
      search: false,
      render: (_, record) => (
        <Tag color={STATUS_COLOR[record.status] || 'default'} style={{ margin: 0 }}>
          {record.status}
        </Tag>
      ),
    },
    {
      title: '命令',
      dataIndex: 'command',
      ellipsis: true,
      search: false,
      render: (val) => val ? <span style={{ fontFamily: 'monospace', color: '#595959' }}>{val}</span> : '-',
    },
    {
      title: '耗时',
      dataIndex: 'duration_ms',
      width: 100,
      align: 'right',
      search: false,
      render: (_, record) => {
        if (typeof record.duration_ms !== 'number') return '-';
        return record.duration_ms >= 1000 
          ? `${(record.duration_ms / 1000).toFixed(2)}s` 
          : `${record.duration_ms.toFixed(0)}ms`;
      },
    },
    {
      title: '操作',
      valueType: 'option',
      width: 100,
      align: 'center',
      fixed: 'right',
      render: (_, record) => {
        if (record.event_source !== 'tool') return '-';
        return (
          <Button
            type="link"
            size="small"
            disabled={record.status !== 'running'}
            onClick={() => openStream(record)}
          >
            实时输出
          </Button>
        );
      },
    },
  ];

  return (
    <>
      <ProTable<TaskLogEvent>
        headerTitle="任务日志查询"
        actionRef={actionRef}
        formRef={formRef}
        rowKey="id"
        columns={columns}
        request={async (params) => {
          if (!params.task) {
            return { data: [], total: 0, success: true };
          }
          const res = await listTaskLogs({
            task: params.task,
            stage: params.stage,
            event_source: params.event_source,
            page: params.current,
            page_size: params.pageSize,
          });
          // update real-time status if we are streaming
          if (streamingLog) {
            const matched = res.items.find((item) => item.id === streamingLog.id);
            if (matched && matched.status) {
              setStreamStatus(streamingLog.id, matched.status);
            }
          }
          return { data: res.items, total: res.total, success: true };
        }}
        scroll={{ x: 1200 }}
        pagination={{ defaultPageSize: 20 }}
        toolBarRender={() => [
          <Select 
            key="auto-refresh"
            value={autoRefreshInterval} 
            onChange={setAutoRefreshInterval} 
            options={[
              { label: '自动刷新: 关', value: 0 },
              { label: '3秒刷新', value: 3000 },
              { label: '5秒刷新', value: 5000 },
              { label: '10秒刷新', value: 10000 },
            ]} 
            style={{ width: 120 }} 
          />,
          <Button
            key="refresh"
            icon={<SyncOutlined />}
            onClick={() => actionRef.current?.reload()}
          >
            刷新
          </Button>
        ]}
        locale={{ emptyText: '请在上方选择任务进行查询' }}
        expandable={{
          expandedRowRender: (record) => {
            const tabItems = [];
            if (record.request_body && Object.keys(record.request_body).length > 0) {
              tabItems.push({
                key: 'req',
                label: 'Request',
                children: <CodeBlock content={JSON.stringify(record.request_body, null, 2)} />
              });
            }
            if (record.response_body && Object.keys(record.response_body).length > 0) {
              tabItems.push({
                key: 'res',
                label: 'Response',
                children: <CodeBlock content={JSON.stringify(record.response_body, null, 2)} />
              });
            }
            if (record.command_args && Object.keys(record.command_args).length > 0) {
              tabItems.push({
                key: 'args',
                label: 'Command Args',
                children: <CodeBlock content={JSON.stringify(record.command_args, null, 2)} />
              });
            }
            if (record.result) {
              tabItems.push({
                key: 'resRaw',
                label: 'Execution Result',
                children: <CodeBlock content={record.result} />
              });
            }
            if (record.output_summary) {
              tabItems.push({
                key: 'summary',
                label: 'Output Summary',
                children: (
                  <Space direction="vertical" style={{ width: '100%' }}>
                    {record.output_truncated && (
                      <Alert type="warning" showIcon message="输出过长，已自动截断（最大50KB）。" style={{ marginBottom: 8 }} />
                    )}
                    <CodeBlock content={record.output_summary} />
                  </Space>
                )
              });
            }

            return (
              <div style={{ padding: '16px', background: '#fcfcfc', border: '1px solid #f0f0f0', borderRadius: 6 }}>
                <Descriptions size="small" column={{ xxl: 3, xl: 3, lg: 3, md: 2, sm: 1, xs: 1 }} style={{ marginBottom: tabItems.length > 0 ? 16 : 0 }}>
                  <Descriptions.Item label="日志ID"><Text copyable>{record.id}</Text></Descriptions.Item>
                  <Descriptions.Item label="关联ID">{record.correlation_id ? <Text copyable>{record.correlation_id}</Text> : '-'}</Descriptions.Item>
                  <Descriptions.Item label="运行模式">{record.execution_mode || '-'}</Descriptions.Item>
                  <Descriptions.Item label="工作空间">
                    {record.workspace ? <Text code copyable>{record.workspace}</Text> : '-'}
                  </Descriptions.Item>
                  <Descriptions.Item label="Agent角色">{record.agent_role || '-'}</Descriptions.Item>
                  {record.missing_fields && record.missing_fields.length > 0 && (
                    <Descriptions.Item label="缺失字段">
                      <Text type="danger">{record.missing_fields.join(', ')}</Text>
                    </Descriptions.Item>
                  )}
                </Descriptions>
                
                {tabItems.length > 0 && (
                  <Tabs size="small" items={tabItems} type="card" />
                )}
              </div>
            );
          },
        }}
      />

      <Drawer
        title={
          <Space>
            {streamingLog ? `实时输出 - ${streamingLog.command || streamingLog.event_type}` : '实时输出'}
            {streamStatus && (
              <Tag color={STATUS_COLOR[streamStatus] || 'default'} style={{ margin: 0 }}>
                {streamStatus}
              </Tag>
            )}
          </Space>
        }
        width={720}
        open={Boolean(streamingLog)}
        onClose={closeStream}
        styles={{ body: { paddingBottom: 24 } }}
      >
        {streamingLog ? (
          <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
            <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
              <Space direction="vertical" size={4}>
                <Text type="secondary" style={{ fontSize: 13 }}>日志ID: {streamingLog.id}</Text>
                {TERMINAL_STREAM_STATUS.has(streamStatus || '') ? (
                  <Text type="secondary" style={{ fontSize: 13 }}>执行已结束，列表会显示最终状态和摘要。</Text>
                ) : (
                  <Text type="secondary" style={{ fontSize: 13 }}>仅显示你打开该面板后的新增输出，不回放历史内容。</Text>
                )}
              </Space>
            </div>
            <div style={{ position: 'relative', flex: 1, minHeight: 0 }}>
              <pre
                ref={streamPreRef}
                style={{
                  margin: 0,
                  height: '100%',
                  overflow: 'auto',
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                  background: '#1e1e1e',
                  color: '#d4d4d4',
                  border: '1px solid #333',
                  borderRadius: 6,
                  padding: 12,
                  fontFamily: 'SFMono-Regular, Consolas, "Liberation Mono", Menlo, Courier, monospace',
                  fontSize: 13,
                  lineHeight: 1.5,
                }}
              >
                {streamLines.length > 0 ? streamLines.join('') : '等待运行中输出...'}
              </pre>
            </div>
          </div>
        ) : null}
      </Drawer>
    </>
  );
};

export default TaskLogsPage;
