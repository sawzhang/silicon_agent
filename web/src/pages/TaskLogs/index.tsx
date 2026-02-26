import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Alert, Button, Card, Drawer, Form, Select, Space, Table, Tag, Typography } from 'antd';
import type { ColumnsType, TablePaginationConfig } from 'antd/es/table';
import { listTaskLogs, type TaskLogEvent } from '@/services/taskLogApi';
import { getTaskStages, listTasks } from '@/services/taskApi';
import { listProjects } from '@/services/projectApi';
import { useTaskLogStreamStore } from '@/stores/taskLogStreamStore';
import { formatTimestamp } from '@/utils/formatters';

const { Text } = Typography;

type QueryState = {
  project?: string;
  task: string;
  stage?: string;
  event_source?: string;
};

type SelectOption = {
  label: string;
  value: string;
};

const EVENT_SOURCE_OPTIONS = [
  { label: '全部', value: '' },
  { label: 'LLM', value: 'llm' },
  { label: 'Tool', value: 'tool' },
  { label: '系统', value: 'system' },
];

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

const TaskLogsPage: React.FC = () => {
  const [form] = Form.useForm<QueryState>();
  const [query, setQuery] = useState<QueryState | null>(null);
  const [rows, setRows] = useState<TaskLogEvent[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string>('');
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [projectOptions, setProjectOptions] = useState<SelectOption[]>([]);
  const [taskOptions, setTaskOptions] = useState<SelectOption[]>([]);
  const [projectLoading, setProjectLoading] = useState(false);
  const [taskLoading, setTaskLoading] = useState(false);
  const [stageOptions, setStageOptions] = useState<SelectOption[]>([]);
  const [stageLoading, setStageLoading] = useState(false);
  const [streamingLog, setStreamingLog] = useState<TaskLogEvent | null>(null);
  const taskValue = Form.useWatch('task', form);
  const projectValue = Form.useWatch('project', form);
  const linesByLog = useTaskLogStreamStore((s) => s.linesByLog);
  const statusByLog = useTaskLogStreamStore((s) => s.statusByLog);
  const subscribeStream = useTaskLogStreamStore((s) => s.subscribe);
  const unsubscribeStream = useTaskLogStreamStore((s) => s.unsubscribe);
  const clearStream = useTaskLogStreamStore((s) => s.clear);
  const setStreamStatus = useTaskLogStreamStore((s) => s.setStatus);

  const fetchLogs = useCallback(async (q: QueryState, nextPage: number, nextPageSize: number) => {
    setLoading(true);
    setError('');
    try {
      const result = await listTaskLogs({
        task: q.task,
        stage: q.stage || undefined,
        event_source: q.event_source || undefined,
        page: nextPage,
        page_size: nextPageSize,
      });
      setRows(result.items);
      setTotal(result.total);
    } catch (err: any) {
      const detail = err?.response?.data?.detail || err?.message || '加载日志失败';
      setError(String(detail));
      setRows([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, []);

  const loadProjectOptions = useCallback(async (keyword: string) => {
    setProjectLoading(true);
    try {
      const result = await listProjects({
        page: 1,
        page_size: 20,
        name: keyword.trim() || undefined,
      });
      const nextOptions = result.items.map((item) => ({
        value: item.id,
        label: item.display_name,
      }));
      setProjectOptions(nextOptions);
    } catch {
      setProjectOptions([]);
    } finally {
      setProjectLoading(false);
    }
  }, []);

  const loadTaskOptions = useCallback(async (keyword: string, projectId?: string) => {
    setTaskLoading(true);
    try {
      const result = await listTasks({
        page: 1,
        page_size: 20,
        project_id: projectId?.trim() || undefined,
        title: keyword.trim() || undefined,
      });
      const nextOptions = result.tasks.map((item) => ({
        value: item.id,
        label: `${item.title} (${item.id.slice(0, 8)})`,
      }));
      setTaskOptions(nextOptions);
    } catch {
      setTaskOptions([]);
    } finally {
      setTaskLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!query) return;
    void fetchLogs(query, page, pageSize);
  }, [fetchLogs, page, pageSize, query]);

  useEffect(() => {
    void loadProjectOptions('');
  }, [loadProjectOptions]);

  useEffect(() => {
    form.setFieldValue('task', undefined);
    form.setFieldValue('stage', undefined);
    setTaskOptions([]);
    setStageOptions([]);
    void loadTaskOptions('', projectValue);
  }, [form, loadTaskOptions, projectValue]);

  useEffect(() => {
    const taskId = (taskValue || '').trim();
    if (!taskId) {
      setStageOptions([]);
      form.setFieldValue('stage', undefined);
      return;
    }

    let canceled = false;
    const timer = window.setTimeout(async () => {
      setStageLoading(true);
      try {
        const stages = await getTaskStages(taskId);
        if (canceled) return;

        const uniqueStageNames = Array.from(
          new Set(stages.map((item) => item.stage_name).filter(Boolean)),
        );
        setStageOptions(uniqueStageNames.map((name) => ({ label: name, value: name })));

        const selected = form.getFieldValue('stage');
        if (selected && !uniqueStageNames.includes(selected)) {
          form.setFieldValue('stage', undefined);
        }
      } catch {
        if (!canceled) {
          setStageOptions([]);
        }
      } finally {
        if (!canceled) {
          setStageLoading(false);
        }
      }
    }, 300);

    return () => {
      canceled = true;
      window.clearTimeout(timer);
    };
  }, [form, taskValue]);

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
    if (!streamingLog || !streamStatus || !query) return;
    if (!TERMINAL_STREAM_STATUS.has(streamStatus)) return;
    void fetchLogs(query, page, pageSize);
  }, [fetchLogs, page, pageSize, query, streamStatus, streamingLog]);

  useEffect(() => {
    if (!streamingLog) return;
    const matched = rows.find((item) => item.id === streamingLog.id);
    if (!matched || !matched.status) return;
    setStreamStatus(streamingLog.id, matched.status);
  }, [rows, setStreamStatus, streamingLog]);

  useEffect(() => {
    if (!streamingLog || !query) return;
    if (TERMINAL_STREAM_STATUS.has(streamStatus || '')) return;
    const timer = window.setInterval(() => {
      void fetchLogs(query, page, pageSize);
    }, 3000);
    return () => {
      window.clearInterval(timer);
    };
  }, [fetchLogs, page, pageSize, query, streamStatus, streamingLog]);

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

  const columns: ColumnsType<TaskLogEvent> = useMemo(
    () => [
      {
        title: '序号',
        dataIndex: 'event_seq',
        width: 90,
        align: 'right',
      },
      {
        title: '时间',
        dataIndex: 'created_at',
        width: 180,
        render: (_, record) => formatTimestamp(record.created_at),
      },
      {
        title: '来源',
        dataIndex: 'event_source',
        width: 110,
        render: (_, record) => (
          <Tag color={SOURCE_COLOR[record.event_source] || 'default'}>
            {record.event_source.toUpperCase()}
          </Tag>
        ),
      },
      {
        title: '类型',
        dataIndex: 'event_type',
        width: 180,
      },
      {
        title: '状态',
        dataIndex: 'status',
        width: 100,
        render: (_, record) => (
          <Tag color={STATUS_COLOR[record.status] || 'default'}>{record.status}</Tag>
        ),
      },
      {
        title: '命令',
        dataIndex: 'command',
        ellipsis: true,
      },
      {
        title: '工作空间',
        dataIndex: 'workspace',
        ellipsis: true,
      },
      {
        title: '耗时(ms)',
        dataIndex: 'duration_ms',
        width: 120,
        align: 'right',
        render: (_, record) => (typeof record.duration_ms === 'number' ? record.duration_ms.toFixed(2) : '-'),
      },
      {
        title: '实时输出',
        width: 120,
        render: (_, record) => {
          if (record.event_source !== 'tool') {
            return '-';
          }
          return (
            <Button
              size="small"
              disabled={record.status !== 'running'}
              onClick={() => openStream(record)}
            >
              查看流
            </Button>
          );
        },
      },
    ],
    [openStream],
  );

  const onSearch = async () => {
    const values = await form.validateFields();
    const nextQuery: QueryState = {
      project: values.project ? values.project.trim() : undefined,
      task: values.task.trim(),
      stage: values.stage ? values.stage.trim() : undefined,
      event_source: values.event_source,
    };
    setQuery(nextQuery);
    setPage(1);
    await fetchLogs(nextQuery, 1, pageSize);
  };

  const onTableChange = (pagination: TablePaginationConfig) => {
    setPage(pagination.current || 1);
    setPageSize(pagination.pageSize || 20);
  };

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card title="任务日志查询">
        <Form form={form} layout="inline" initialValues={{ event_source: '' }}>
          <Form.Item
            label="项目"
            name="project"
          >
            <Select
              allowClear
              showSearch
              filterOption={false}
              loading={projectLoading}
              options={projectOptions}
              placeholder="可选，按项目名称搜索"
              onSearch={(value) => {
                void loadProjectOptions(value);
              }}
              onFocus={() => {
                if (projectOptions.length === 0) {
                  void loadProjectOptions('');
                }
              }}
              style={{ width: 260 }}
            />
          </Form.Item>
          <Form.Item
            label="任务"
            name="task"
            rules={[{ required: true, message: '请选择任务' }]}
          >
            <Select
              allowClear
              showSearch
              filterOption={false}
              loading={taskLoading}
              options={taskOptions}
              placeholder="必填，按任务标题模糊搜索"
              onSearch={(value) => {
                void loadTaskOptions(value, projectValue);
              }}
              onFocus={() => {
                if (taskOptions.length === 0) {
                  void loadTaskOptions('', projectValue);
                }
              }}
              style={{ width: 340 }}
            />
          </Form.Item>
          <Form.Item
            label="阶段"
            name="stage"
          >
            <Select
              allowClear
              showSearch
              loading={stageLoading}
              options={stageOptions}
              placeholder="可选，先选择任务自动加载"
              style={{ width: 260 }}
            />
          </Form.Item>
          <Form.Item label="来源" name="event_source">
            <Select style={{ width: 140 }} options={EVENT_SOURCE_OPTIONS} />
          </Form.Item>
          <Form.Item>
            <Button type="primary" onClick={() => void onSearch()}>
              查询日志
            </Button>
          </Form.Item>
        </Form>
      </Card>

      {error ? <Alert type="error" showIcon message={error} /> : null}

      <Card title="日志明细">
        <Table<TaskLogEvent>
          rowKey="id"
          loading={loading}
          columns={columns}
          dataSource={rows}
          pagination={{ current: page, pageSize, total, showSizeChanger: true }}
          onChange={onTableChange}
          locale={{
            emptyText: query ? '没有匹配日志' : '请选择任务后查询（项目和阶段可选）',
          }}
          expandable={{
            expandedRowRender: (record) => (
              <Space direction="vertical" size={12} style={{ width: '100%' }}>
                {record.request_body ? (
                  <div>
                    <Text strong>Request Body</Text>
                    <pre style={{ margin: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                      {JSON.stringify(record.request_body, null, 2)}
                    </pre>
                  </div>
                ) : null}
                {record.response_body ? (
                  <div>
                    <Text strong>Response Body</Text>
                    <pre style={{ margin: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                      {JSON.stringify(record.response_body, null, 2)}
                    </pre>
                  </div>
                ) : null}
                {record.command_args ? (
                  <div>
                    <Text strong>命令参数</Text>
                    <pre style={{ margin: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                      {JSON.stringify(record.command_args, null, 2)}
                    </pre>
                  </div>
                ) : null}
                {record.result ? (
                  <div>
                    <Text strong>执行结果</Text>
                    <pre style={{ margin: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                      {record.result}
                    </pre>
                  </div>
                ) : null}
                {record.output_summary ? (
                  <div>
                    <Text strong>输出摘要</Text>
                    <pre style={{ margin: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                      {record.output_summary}
                    </pre>
                    {record.output_truncated ? <Text type="warning">输出已截断（50KB）</Text> : null}
                  </div>
                ) : null}
                {record.correlation_id ? (
                  <Text type="secondary">关联ID: {record.correlation_id}</Text>
                ) : null}
                {record.missing_fields?.length ? (
                  <Text type="warning">缺失字段: {record.missing_fields.join(', ')}</Text>
                ) : null}
              </Space>
            ),
          }}
        />
      </Card>

      <Drawer
        title={streamingLog ? `实时输出 - ${streamingLog.command || streamingLog.event_type}` : '实时输出'}
        width={720}
        open={Boolean(streamingLog)}
        onClose={closeStream}
      >
        {streamingLog ? (
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Space>
              <Text strong>日志ID</Text>
              <Text code>{streamingLog.id}</Text>
            </Space>
            <Space>
              <Text strong>状态</Text>
              <Tag color={STATUS_COLOR[streamStatus || streamingLog.status] || 'default'}>
                {streamStatus || streamingLog.status}
              </Tag>
            </Space>
            <pre
              style={{
                margin: 0,
                minHeight: 320,
                maxHeight: '65vh',
                overflow: 'auto',
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
                background: '#fafafa',
                border: '1px solid #f0f0f0',
                borderRadius: 6,
                padding: 12,
              }}
            >
              {streamLines.length > 0 ? streamLines.join('') : '等待运行中输出...'}
            </pre>
            {TERMINAL_STREAM_STATUS.has(streamStatus || '') ? (
              <Text type="secondary">执行已结束，列表会显示最终状态和摘要。</Text>
            ) : (
              <Text type="secondary">仅显示你打开该面板后的新增输出，不回放历史内容。</Text>
            )}
          </Space>
        ) : null}
      </Drawer>
    </Space>
  );
};

export default TaskLogsPage;
