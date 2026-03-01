import React, { useEffect, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Card, Collapse, Descriptions, Empty, Tag, Button, Spin, Typography, Space, message, Timeline, Tabs } from 'antd';
import { ArrowLeftOutlined, StopOutlined, ReloadOutlined, CodeOutlined, RobotOutlined, LoadingOutlined, DownOutlined, LeftOutlined } from '@ant-design/icons';
import { useTask, useCancelTask, useRetryTaskFromStage } from '@/hooks/useTasks';
import { useStageLogStore } from '@/stores/stageLogStore';
import { listTaskLogs } from '@/services/taskLogApi';
import { useGateList } from '@/hooks/useGates';
import PipelineView from '@/components/PipelineView';
import ReActTimeline from '@/components/ReActTimeline';
import { STAGE_NAMES } from '@/utils/constants';
import { formatTimestamp, formatTokens, formatCost, formatDuration } from '@/utils/formatters';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

const { Title } = Typography;

const STATUS_COLOR: Record<string, string> = {
  pending: 'default',
  running: 'processing',
  completed: 'success',
  failed: 'error',
  cancelled: 'warning',
  skipped: 'default',
  planning: 'warning',
};

const STAGE_DISPLAY: Record<string, string> = Object.fromEntries(
  STAGE_NAMES.map((sn) => [sn.key, sn.name])
);

const EVENT_ICONS: Record<string, React.ReactNode> = {
  tool_call_executed: <CodeOutlined style={{ color: '#1890ff' }} />,
  llm_response_received: <RobotOutlined style={{ color: '#52c41a' }} />,
  llm_request_sent: <LoadingOutlined style={{ color: '#faad14' }} />,
};

const EMPTY_LOGS: import('@/types/websocket').WSStageLogPayload[] = [];

const StageLiveLog: React.FC<{ stageId: string }> = ({ stageId }) => {
  const logs = useStageLogStore((s) => s.logsByStage[stageId] ?? EMPTY_LOGS);
  const containerRef = useRef<HTMLDivElement>(null);
  const logCount = logs.length;

  useEffect(() => {
    const el = containerRef.current;
    if (el) {
      el.scrollTop = el.scrollHeight;
    }
  }, [logCount]);

  if (logCount === 0) {
    return (
      <div style={{ textAlign: 'center', padding: '24px 0' }}>
        <LoadingOutlined style={{ fontSize: 24, marginBottom: 8 }} />
        <div style={{ color: '#999' }}>等待执行日志...</div>
      </div>
    );
  }

  return (
    <div ref={containerRef} style={{ maxHeight: 400, overflow: 'auto', padding: '0 8px' }}>
      <Timeline
        items={logs.map((log, i) => ({
          key: i,
          dot: EVENT_ICONS[log.event_type] || EVENT_ICONS.llm_request_sent,
          children: (
            <div style={{ fontSize: 13 }}>
              <div>
                <Tag color={log.status === 'success' ? 'green' : log.status === 'failed' ? 'red' : 'blue'} style={{ fontSize: 11 }}>
                  {log.event_source}
                </Tag>
                <span style={{ fontWeight: 500 }}>{log.command || log.event_type}</span>
                {log.duration_ms != null && (
                  <span style={{ color: '#999', marginLeft: 8 }}>{(log.duration_ms / 1000).toFixed(1)}s</span>
                )}
              </div>
              {log.result_preview && (
                <pre style={{ marginTop: 4, marginBottom: 0, fontSize: 12, color: '#666', whiteSpace: 'pre-wrap', maxHeight: 80, overflow: 'hidden' }}>
                  {log.result_preview}
                </pre>
              )}
            </div>
          ),
        }))}
      />
    </div>
  );
};

const StageReActDetails: React.FC<{ taskId: string; stageId: string; stageName: string; isRunning: boolean }> = ({ taskId, stageId, stageName, isRunning }) => {
  const liveLogs = useStageLogStore((s) => s.logsByStage[stageId] ?? EMPTY_LOGS);

  // Fetch historical logs for this stage
  const { data, isLoading } = useQuery({
    queryKey: ['taskLogs', taskId, stageName],
    queryFn: () => listTaskLogs({ task: taskId, stage: stageName, page_size: 500 }),
    enabled: !!taskId && !!stageName,
    refetchInterval: isRunning ? 3000 : false, // Poll if still running to get the latest DB state
  });

  const historicalLogs = data?.items || [];

  // Merge: Since our ReActTimeline handles all event types natively based on correlation mapping,
  // we mainly rely on historicalLogs (DB state) which is richer.
  // The 'liveLogs' from WebSocket are simpler StageLogPayloads. 
  // In a robust implementation, we'd map WS to TaskLogEvents, but since we poll when running,
  // passing historicalLogs covers most of the ReAct rendering nicely.

  return <ReActTimeline logs={historicalLogs} loading={isLoading} />;
};

const ExpandableReport: React.FC<{ content: string; maxHeight?: number }> = ({ content, maxHeight = 400 }) => {
  const [expanded, setExpanded] = React.useState(false);
  const [isOverflowing, setIsOverflowing] = React.useState(false);
  const contentRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (contentRef.current) {
      setIsOverflowing(contentRef.current.scrollHeight > maxHeight);
    }
  }, [content, maxHeight]);

  return (
    <div style={{ position: 'relative' }}>
      {isOverflowing && (
        <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 8 }}>
          <Button
            type="link"
            size="small"
            onClick={() => setExpanded(!expanded)}
            style={{ fontSize: 13, color: '#8c8c8c', padding: 0 }}
            icon={expanded ? <DownOutlined /> : <LeftOutlined />}
          >
            {expanded ? 'Collapse all' : 'Expand all'}
          </Button>
        </div>
      )}
      <div
        className="markdown-body"
        style={{
          padding: 16,
          backgroundColor: '#f9f9f9',
          borderRadius: 8,
          border: '1px solid #eee',
          maxHeight: expanded ? 'none' : maxHeight,
          overflow: 'hidden',
          position: 'relative'
        }}
      >
        <div ref={contentRef}>
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
        </div>
        {!expanded && isOverflowing && (
          <div
            style={{
              position: 'absolute',
              bottom: 0,
              left: 0,
              right: 0,
              height: 40,
              background: 'linear-gradient(transparent, #f9f9f9)',
              pointerEvents: 'none',
              borderRadius: '0 0 8px 8px'
            }}
          />
        )}
      </div>
    </div>
  );
};

const TaskDetail: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { data: task, isLoading } = useTask(id!);
  const { data: gates } = useGateList({ task_id: id });

  const getGateForStage = (stage: any, index: number, allStages: any[]) => {
    if (!gates || !gates.length) return null;

    // Find all gates that were triggered by this specific stage
    const candidateGates = gates.filter(gate => {
      const gateTriggerStageName = gate.content?.stage;
      if (!gateTriggerStageName) return false;
      return stage.stage_name === gateTriggerStageName;
    });

    if (candidateGates.length === 0) return null;

    // Return the latest gate for this stage
    return candidateGates.sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())[0];
  };

  const cancelTask = useCancelTask();
  const retryTaskFromStage = useRetryTaskFromStage();

  if (isLoading || !task) {
    return <Spin size="large" style={{ display: 'block', margin: '100px auto' }} />;
  }

  const duration = task.created_at && task.completed_at
    ? (new Date(task.completed_at).getTime() - new Date(task.created_at).getTime()) / 1000
    : null;
  const firstFailedStage = task.stages.find((stage) => stage.status === 'failed');

  return (
    <div>
      <Space style={{ marginBottom: 16 }}>
        <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/tasks')}>
          返回
        </Button>
        {(task.status === 'running' || task.status === 'pending') && (
          <Button
            danger
            icon={<StopOutlined />}
            onClick={async () => {
              await cancelTask.mutateAsync(task.id);
              message.success('任务已取消');
            }}
            loading={cancelTask.isPending}
          >
            取消任务
          </Button>
        )}
        {task.status === 'failed' && (
          <Button
            type="primary"
            icon={<ReloadOutlined />}
            onClick={async () => {
              if (!firstFailedStage) {
                message.warning('未找到可重试的失败节点');
                return;
              }
              await retryTaskFromStage.mutateAsync({
                id: task.id,
                req: { stage_id: firstFailedStage.id },
              });
              message.success('任务已重新提交，将从失败节点继续执行');
            }}
            loading={retryTaskFromStage.isPending}
          >
            从失败节点重试
          </Button>
        )}
      </Space>

      <Title level={4}>{task.title}</Title>

      <Card style={{ marginBottom: 16 }}>
        <PipelineView stages={task.stages.map((stage, idx) => {
          const gate = getGateForStage(stage, idx, task.stages);
          const isPendingGate = gate?.status === 'pending';
          return isPendingGate ? { ...stage, status: 'running' as any } : stage;
        })} />
      </Card>

      {task.stages.length > 0 && (
        <Card title="阶段产出" style={{ marginBottom: 16 }}>
          <Collapse accordion>
            {task.stages.map((stage, idx) => {
              const latestGate = getGateForStage(stage, idx, task.stages);
              const isPendingGate = latestGate?.status === 'pending';
              const displayStatus = isPendingGate ? 'running' : stage.status;

              return (
                <Collapse.Panel
                  key={stage.id}
                  header={
                    <Space>
                      <Tag color={STATUS_COLOR[displayStatus]}>
                        {isPendingGate ? '等待审批' : STATUS_COLOR[displayStatus] === 'processing' ? '运行中' : STATUS_COLOR[displayStatus] === 'success' ? '已完成' : STATUS_COLOR[displayStatus] === 'error' ? '失败' : STATUS_COLOR[displayStatus] === 'warning' ? '已取消/已跳过' : displayStatus}
                      </Tag>
                      <span>{STAGE_DISPLAY[stage.stage_name] || stage.stage_name}</span>
                      <span style={{ color: '#999' }}>
                        {stage.tokens_used > 0 && `${stage.tokens_used.toLocaleString()} tokens`}
                        {stage.duration_seconds != null && ` · ${stage.duration_seconds.toFixed(1)}s`}
                      </span>
                      {(() => {
                        const gate = latestGate;
                        if (!gate) return null;

                        // Only show gate UI if:
                        // 1. The gate is currently pending (blocking execution).
                        // 2. OR the gate is resolved AND the stage has started/finished.
                        // This prevents showing stale/irrelevant gate results on pending stages after a task retry.
                        if (gate.status !== 'pending' && stage.status === 'pending') return null;

                        let bgColor = '#fffbe6';
                        let borderColor = '#ffe58f';
                        let textColor = '#faad14';
                        let label = gate.gate_type === 'plan_review' ? '等待方案审批' : '等待结果审批';
                        let text = gate.gate_type === 'plan_review' ? '该方案正等待人工介入确认。' : '执行结果正等待人工介入确认。';
                        let actionText: string | null = gate.status === 'pending' ? '去审批' : null;

                        if (gate.status === 'approved') {
                          bgColor = '#f6ffed';
                          borderColor = '#b7eb8f';
                          textColor = '#52c41a';
                          label = gate.gate_type === 'plan_review' ? '方案已通过' : '结果已通过';
                          text = gate.gate_type === 'plan_review' ? '方案已获人工审批确认。' : '执行结果已获人工审批确认。';
                          actionText = '查看审批';
                        } else if (gate.status === 'rejected') {
                          bgColor = '#fff2f0';
                          borderColor = '#ffccc7';
                          textColor = '#f5222d';
                          label = gate.gate_type === 'plan_review' ? '方案被驳回' : '结果被驳回';
                          text = gate.gate_type === 'plan_review' ? '方案已被人工审批驳回。' : '执行结果已被人工审批驳回。';
                          actionText = '查看审批';
                        } else if (gate.status === 'revised') {
                          bgColor = '#e6f4ff';
                          borderColor = '#91caff';
                          textColor = '#1677ff';
                          label = gate.gate_type === 'plan_review' ? '方案已修订' : '结果已修订';
                          text = gate.gate_type === 'plan_review' ? '方案已由人工手动修订。' : '执行结果已由人工手动修订。';
                          actionText = '查看审批';
                        }

                        return (
                          <span
                            style={{ marginLeft: 16, fontSize: 13, backgroundColor: bgColor, padding: '2px 8px', borderRadius: 4, border: `1px solid ${borderColor}` }}
                            onClick={(e) => e.stopPropagation()}
                          >
                            <span style={{ color: textColor, fontWeight: 500, marginRight: 8 }}>{label}</span>
                            <span style={{ color: '#666' }}>{text}</span>
                            {actionText && (
                              <a
                                onClick={(e) => {
                                  e.preventDefault();
                                  navigate(`/gates?status=all#gate-card-${gate.id}`);
                                }}
                                style={{ marginLeft: 8 }}
                              >
                                {actionText} →
                              </a>
                            )}
                          </span>
                        );
                      })()}
                    </Space>
                  }
                >
                  {/* Top Section: High-level metadata (Badges, Retries, Failure Category) */}
                  <div style={{ marginBottom: 12 }}>
                    <Space wrap>
                      {/* Phase 1.1: Structured output badges */}
                      {stage.output_structured && (
                        <>
                          <Tag color={stage.output_structured.status === 'pass' ? 'green' : stage.output_structured.status === 'fail' ? 'red' : 'orange'}>
                            {stage.output_structured.status}
                          </Tag>
                          {stage.output_structured.confidence != null && (
                            <Tag color={stage.output_structured.confidence >= 0.7 ? 'green' : stage.output_structured.confidence >= 0.5 ? 'orange' : 'red'}>
                              信心: {Math.round(stage.output_structured.confidence * 100)}%
                            </Tag>
                          )}
                          {/* Stage-specific badges */}
                          {(stage.output_structured as Record<string, unknown>).tests_passed != null && (
                            <Tag color="green">通过: {String((stage.output_structured as Record<string, unknown>).tests_passed)}</Tag>
                          )}
                          {(stage.output_structured as Record<string, unknown>).tests_failed != null && Number((stage.output_structured as Record<string, unknown>).tests_failed) > 0 && (
                            <Tag color="red">失败: {String((stage.output_structured as Record<string, unknown>).tests_failed)}</Tag>
                          )}
                          {(stage.output_structured as Record<string, unknown>).issues_critical != null && Number((stage.output_structured as Record<string, unknown>).issues_critical) > 0 && (
                            <Tag color="red">严重缺陷: {String((stage.output_structured as Record<string, unknown>).issues_critical)}</Tag>
                          )}
                          {(stage.output_structured as Record<string, unknown>).issues_major != null && Number((stage.output_structured as Record<string, unknown>).issues_major) > 0 && (
                            <Tag color="orange">主要缺陷: {String((stage.output_structured as Record<string, unknown>).issues_major)}</Tag>
                          )}
                          {stage.output_structured.artifacts && stage.output_structured.artifacts.length > 0 && (
                            <Tag>{stage.output_structured.artifacts.length} 文件</Tag>
                          )}
                        </>
                      )}
                      {/* Phase 1.2: Failure category badge */}
                      {stage.failure_category && (
                        <Tag color="volcano">{stage.failure_category}</Tag>
                      )}
                      {/* Phase 2.5: Retry count */}
                      {stage.retry_count > 0 && (
                        <Tag>重试 {stage.retry_count}</Tag>
                      )}
                    </Space>

                  </div>

                  <Tabs
                    defaultActiveKey="react"
                    items={[
                      {
                        key: 'react',
                        label: '推演过程 (ReAct Track)',
                        children: (
                          <div style={{ padding: '8px 0' }}>
                            <StageReActDetails
                              taskId={task.id}
                              stageId={stage.id}
                              stageName={stage.stage_name}
                              isRunning={stage.status === 'running'}
                            />
                          </div>
                        ),
                      },
                      {
                        key: 'output',
                        label: '执行报告',
                        children: (
                          <div style={{ overflow: 'auto' }}>
                            {stage.output_summary ? (
                              <ExpandableReport content={stage.output_summary} />
                            ) : stage.error_message ? (
                              <div style={{ padding: 16 }}>
                                <Typography.Text type="danger" style={{ display: 'block', marginBottom: 12 }}>
                                  {stage.error_message}
                                </Typography.Text>
                                {task.status === 'failed' && stage.status === 'failed' && (
                                  <Button
                                    type="primary"
                                    size="small"
                                    icon={<ReloadOutlined />}
                                    onClick={async () => {
                                      await retryTaskFromStage.mutateAsync({
                                        id: task.id,
                                        req: { stage_id: stage.id },
                                      });
                                      message.success('任务已重新提交，将从失败节点继续执行');
                                    }}
                                    loading={retryTaskFromStage.isPending}
                                  >
                                    从此阶段重试
                                  </Button>
                                )}
                              </div>
                            ) : stage.status === 'running' ? (
                              <div style={{ padding: 16 }}>
                                <StageLiveLog stageId={stage.id} />
                              </div>
                            ) : stage.status === 'skipped' ? (
                              <div style={{ padding: 32, color: '#999', fontStyle: 'italic', textAlign: 'center' }}>
                                条件不满足，阶段已跳过
                              </div>
                            ) : (
                              <Empty description="暂无产出摘要" style={{ margin: '32px 0' }} />
                            )}
                          </div>
                        ),
                      },
                    ]}
                  />
                </Collapse.Panel>
              );
            })}
          </Collapse>
        </Card>
      )}

      <Card title="任务详情" style={{ marginBottom: 16 }}>
        <Descriptions column={2}>
          <Descriptions.Item label="ID">{task.id}</Descriptions.Item>
          <Descriptions.Item label="状态">
            <Tag color={STATUS_COLOR[task.status]}>
              {task.status === 'pending' ? '待处理' : task.status === 'running' ? '运行中' : task.status === 'completed' ? '已完成' : task.status === 'failed' ? '失败' : '已取消'}
            </Tag>
          </Descriptions.Item>
          <Descriptions.Item label="模板">{task.template_name || '-'}</Descriptions.Item>
          <Descriptions.Item label="项目">{task.project_name || '-'}</Descriptions.Item>
          {task.target_branch && (
            <Descriptions.Item label="目标分支">
              <Tag icon={<CodeOutlined />}>{task.target_branch}</Tag>
            </Descriptions.Item>
          )}
          <Descriptions.Item label="创建时间">{formatTimestamp(task.created_at)}</Descriptions.Item>
          <Descriptions.Item label="完成时间">{task.completed_at ? formatTimestamp(task.completed_at) : '-'}</Descriptions.Item>
          <Descriptions.Item label="耗时">{duration != null ? formatDuration(duration) : '-'}</Descriptions.Item>
          <Descriptions.Item label="总 Tokens">{formatTokens(task.total_tokens)}</Descriptions.Item>
          <Descriptions.Item label="总成本">{formatCost(task.total_cost_rmb)}</Descriptions.Item>
        </Descriptions>
      </Card>

      <Card title="任务描述">
        <ExpandableReport content={task.description || '-'} maxHeight={320} />
      </Card>
    </div>
  );
};

export default TaskDetail;
