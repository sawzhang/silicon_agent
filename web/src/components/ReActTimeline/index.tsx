import React, { useMemo, useState, useRef, useEffect } from 'react';
import { Collapse, Space, Typography, Tag, Avatar } from 'antd';
import {
    RobotOutlined,
    ToolOutlined,
    CheckCircleOutlined,
    CloseCircleOutlined,
    SyncOutlined,
    UserOutlined,
    RightOutlined,
    DownOutlined,
    LeftOutlined,
} from '@ant-design/icons';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useTaskLogStreamStore } from '@/stores/taskLogStreamStore';
import type { TaskLogEvent } from '@/services/taskLogApi';
import './styles.css';

const { Text, Paragraph } = Typography;

interface ReActTurn {
    id: string;
    turnNumber: number;
    prompt?: TaskLogEvent;
    thought_sent?: TaskLogEvent;
    thought?: TaskLogEvent;
    action?: TaskLogEvent;
    observation?: TaskLogEvent;
}

interface ReActViewProps {
    logs: TaskLogEvent[];
    loading?: boolean;
}

function getLogContent(log?: TaskLogEvent): string {
    if (!log || !log.response_body) return '';
    const raw = (log.response_body as Record<string, unknown>).content;
    if (typeof raw === 'string') return raw.trim();
    if (Array.isArray(raw)) {
        return raw
            .map((item) => {
                if (typeof item === 'string') return item;
                if (item && typeof item === 'object') {
                    const record = item as Record<string, unknown>;
                    if (typeof record.text === 'string') return record.text;
                    if (typeof record.content === 'string') return record.content;
                }
                return '';
            })
            .filter(Boolean)
            .join('\n')
            .trim();
    }
    return '';
}

function parseReActTurns(logs: TaskLogEvent[]): ReActTurn[] {
    const sorted = [...logs].sort((a, b) => a.event_seq - b.event_seq);
    const turnsMap = new Map<string, ReActTurn>();
    let currentTurnNumber = 1;

    for (const log of sorted) {
        let key = log.correlation_id || log.id;

        if (!turnsMap.has(key)) {
            turnsMap.set(key, { id: key, turnNumber: currentTurnNumber++ });
        }
        const turn = turnsMap.get(key)!;

        // ... existing assignments ...
        if (log.event_type === 'agent_runner_chat_sent') {
            turn.prompt = log;
        } else if (log.event_type === 'llm_turn_sent') {
            turn.thought_sent = log;
        } else if (log.event_type === 'llm_turn_received') {
            // Keep the latest turn that actually has content, avoiding empty turns overriding rich content.
            const incomingContent = getLogContent(log);
            const existingContent = getLogContent(turn.thought);
            if (incomingContent || !existingContent) {
                turn.thought = log;
            }
        } else if (log.event_type === 'agent_runner_chat_received') {
            // Fallback only: do not override a meaningful llm_turn_received thought.
            const existingContent = getLogContent(turn.thought);
            if (!existingContent) {
                turn.thought = log;
            }
        } else if (log.event_type === 'tool_call_executed') {
            if (log.status === 'running' && !turn.action) {
                turn.action = log;
            } else {
                turn.observation = log;
                if (!turn.action) turn.action = log;
            }
        }
    }

    return Array.from(turnsMap.values());
}

const ExpandablePromptBlock: React.FC<{ content: string; title: string; maxHeight?: number }> = ({ content, title, maxHeight = 150 }) => {
    const [expanded, setExpanded] = useState(false);
    const [isOverflowing, setIsOverflowing] = useState(false);
    const contentRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        if (contentRef.current) {
            setIsOverflowing(contentRef.current.scrollHeight > maxHeight);
        }
    }, [content, maxHeight]);

    return (
        <div style={{ width: '100%' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                <Text strong className="message-author" style={{ marginBottom: 0 }}>{title}</Text>
                {isOverflowing && (
                    <a
                        onClick={() => setExpanded(!expanded)}
                        style={{ fontSize: 13, userSelect: 'none', color: '#8c8c8c', display: 'flex', alignItems: 'center', gap: 4, visibility: 'visible', textDecoration: 'none' }}
                        className="expand-toggle-link"
                    >
                        {expanded ? 'Collapse all' : 'Expand all'}
                        {expanded ? <DownOutlined style={{ fontSize: 10 }} /> : <LeftOutlined style={{ fontSize: 10 }} />}
                    </a>
                )}
            </div>

            <div className="message-bubble user-bubble" style={{ width: '100%', boxSizing: 'border-box' }}>
                <div
                    style={{
                        maxHeight: expanded ? 'none' : (isOverflowing ? `${maxHeight}px` : 'none'),
                        overflow: 'hidden',
                        position: 'relative'
                    }}
                >
                    <div ref={contentRef} className="markdown-body message-pre" style={{ whiteSpace: 'normal', fontSize: '13px' }}>
                        <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
                    </div>
                </div>
            </div>
        </div>
    );
};

const CursorStyleThoughtBlock: React.FC<{
    thoughtText: string;
    durationMs?: number | null;
    defaultExpanded?: boolean;
}> = ({ thoughtText, durationMs, defaultExpanded = false }) => {
    const [expanded, setExpanded] = useState(defaultExpanded);

    // In some cases duration_ms might be tiny or null; default to <1s or omit
    const seconds = durationMs ? Math.round(durationMs / 1000) : null;
    const label = seconds != null ? `Thought for ${seconds || '< 1'}s` : 'Thought';

    return (
        <div className="cursor-thought-container">
            <div
                className="cursor-thought-header"
                onClick={() => setExpanded(!expanded)}
            >
                {expanded ? <DownOutlined style={{ fontSize: 10, marginRight: 8 }} /> : <RightOutlined style={{ fontSize: 10, marginRight: 8 }} />}
                <Text type="secondary">{label}</Text>
            </div>
            {expanded && (
                <div className="cursor-thought-content markdown-body message-body">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{thoughtText}</ReactMarkdown>
                </div>
            )}
        </div>
    );
};

export const ReActTimeline: React.FC<ReActViewProps> = ({ logs, loading }) => {
    const turns = useMemo(() => parseReActTurns(logs), [logs]);
    const subscribe = useTaskLogStreamStore(state => state.subscribe);
    const unsubscribe = useTaskLogStreamStore(state => state.unsubscribe);
    const linesByLog = useTaskLogStreamStore(state => state.linesByLog);

    useEffect(() => {
        const runningLogs = turns
            .filter(t => t.thought_sent?.status === 'running' && !t.thought)
            .map(t => t.thought_sent!.id);

        for (const logId of runningLogs) {
            subscribe(logId);
        }

        return () => {
            for (const logId of runningLogs) {
                unsubscribe(logId);
            }
        };
    }, [turns, subscribe, unsubscribe]);

    if (loading && logs.length === 0) {
        return (
            <div style={{ textAlign: 'center', padding: '40px 0', color: '#999' }}>
                <SyncOutlined spin style={{ fontSize: 24, marginBottom: 16 }} />
                <div>加载 Agent 推演过程...</div>
            </div>
        );
    }

    if (turns.length === 0) {
        return <div style={{ textAlign: 'center', color: '#999', padding: '40px 0' }}>暂无详细的系统交互或推演数据</div>;
    }

    return (
        <div className="react-gemini-container">
            {turns.map((turn, index) => {
                const isRunning = turn.thought?.status === 'running' || turn.action?.status === 'running' || turn.observation?.status === 'running';

                const promptContent = turn.prompt?.request_body?.prompt as string;

                const isLLMRunning = turn.thought_sent?.status === 'running' && !turn.thought;
                const streamLogId = turn.thought_sent?.id;

                let thoughtText = getLogContent(turn.thought);
                if (isLLMRunning && streamLogId) {
                    const streamLines = linesByLog[streamLogId];
                    if (streamLines && streamLines.length > 0) {
                        thoughtText = streamLines.join('');
                    }
                }

                if (thoughtText && thoughtText.includes('<thought>')) {
                    const match = thoughtText.match(/<thought>([\s\S]*?)(?:<\/thought>|$)/);
                    if (match) {
                        thoughtText = match[1].trim();
                    }
                }

                // AI Response Segment needs to show up if it's currently running, even if text is empty yet
                const hasAIActivity = thoughtText || turn.action?.command || isLLMRunning;

                const actionCommand = turn.action?.command || turn.observation?.command;
                const actionArgs = turn.action?.command_args || turn.observation?.command_args;
                const observationResult = turn.observation?.output_summary || turn.observation?.result;

                const isActionRunning = turn.action?.status === 'running' && turn.observation?.status !== 'success' && turn.observation?.status !== 'failed';
                const isActionSuccess = turn.observation?.status === 'success';
                const isActionFailed = turn.observation?.status === 'failed';

                return (
                    <div key={turn.id} className="react-gemini-turn">

                        {/* 1. Prompt Segment (User/System) */}
                        {promptContent && (
                            <div className="react-gemini-message user-message">
                                <Avatar icon={<UserOutlined />} className="message-avatar" style={{ backgroundColor: '#87d068' }} />
                                <div className="message-content" style={{ minWidth: 0, width: '100%' }}>
                                    <ExpandablePromptBlock content={promptContent} title="System / User" maxHeight={120} />
                                </div>
                            </div>
                        )}

                        {/* 2. AI Response Segment (Thought + Action) */}
                        {hasAIActivity && (
                            <div className="react-gemini-message ai-message">
                                <Avatar icon={<RobotOutlined />} className="message-avatar" style={{ backgroundColor: '#1890ff' }} />
                                <div className="message-content">
                                    <Text strong className="message-author">Silicon Agent</Text>

                                    {/* Thought formatted with Cursor-style collapse */}
                                    {thoughtText && (
                                        <CursorStyleThoughtBlock
                                            thoughtText={thoughtText}
                                            durationMs={turn.thought?.duration_ms || turn.thought_sent?.duration_ms}
                                            defaultExpanded={turn.thought?.event_type === 'llm_turn_received'}
                                        />
                                    )}

                                    {/* Tool Call Collapse mimicking Gemini's "Analyzed..." drop-downs */}
                                    {(actionCommand || actionArgs || observationResult) && (
                                        <Collapse
                                            ghost
                                            size="small"
                                            className="gemini-tool-collapse"
                                            items={[
                                                {
                                                    key: 'tool',
                                                    label: (
                                                        <Space>
                                                            {isActionRunning ? <SyncOutlined spin style={{ color: '#1890ff' }} /> :
                                                                isActionFailed ? <CloseCircleOutlined style={{ color: '#ff4d4f' }} /> :
                                                                    <CheckCircleOutlined style={{ color: '#52c41a' }} />}
                                                            <Text style={{ fontWeight: 500 }}>
                                                                使用工具: {actionCommand || '执行代码'}
                                                            </Text>
                                                        </Space>
                                                    ),
                                                    children: (
                                                        <div className="gemini-tool-details">
                                                            {actionArgs && Object.keys(actionArgs).length > 0 && (
                                                                <div className="gemini-tool-args">
                                                                    <Text type="secondary" style={{ fontSize: 12 }}>Arguments:</Text>
                                                                    <pre>{JSON.stringify(actionArgs, null, 2)}</pre>
                                                                </div>
                                                            )}

                                                            {observationResult && (
                                                                <div className="gemini-tool-observation">
                                                                    <Text type="secondary" style={{ fontSize: 12 }}>Result:</Text>
                                                                    <pre className={isActionFailed ? 'error-result' : ''}>
                                                                        {observationResult}
                                                                    </pre>
                                                                </div>
                                                            )}
                                                        </div>
                                                    )
                                                }
                                            ]}
                                        />
                                    )}

                                    {isRunning && !actionCommand && (
                                        <div className="message-typing-indicator">
                                            <span className="dot"></span>
                                            <span className="dot"></span>
                                            <span className="dot"></span>
                                        </div>
                                    )}
                                </div>
                            </div>
                        )}

                    </div>
                );
            })}
        </div>
    );
};

export default ReActTimeline;
