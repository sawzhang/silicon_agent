import { useEffect, useRef } from 'react';
import { useAgentStore } from '@/stores/agentStore';
import { useActivityStore } from '@/stores/activityStore';
import { useNotificationStore } from '@/stores/notificationStore';
import type { WSMessage, WSAgentStatusPayload, WSActivityPayload, WSGatePayload } from '@/types/websocket';

export function useWebSocket() {
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout>>();
  const heartbeatTimerRef = useRef<ReturnType<typeof setInterval>>();

  const updateAgent = useAgentStore((s) => s.updateAgent);
  const addActivity = useActivityStore((s) => s.addActivity);
  const addNotification = useNotificationStore((s) => s.addNotification);

  useEffect(() => {
    function connect() {
      const wsUrl = import.meta.env.VITE_WS_URL || `ws://${window.location.host}/ws`;
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        console.log('[WS] Connected');
        heartbeatTimerRef.current = setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: 'ping' }));
          }
        }, 30_000);
      };

      ws.onmessage = (event) => {
        try {
          const msg: WSMessage = JSON.parse(event.data);
          handleMessage(msg);
        } catch {
          console.warn('[WS] Failed to parse message');
        }
      };

      ws.onclose = () => {
        console.log('[WS] Disconnected, reconnecting in 3s...');
        cleanup();
        reconnectTimerRef.current = setTimeout(connect, 3_000);
      };

      ws.onerror = () => {
        ws.close();
      };
    }

    function cleanup() {
      if (heartbeatTimerRef.current) clearInterval(heartbeatTimerRef.current);
    }

    function handleMessage(msg: WSMessage) {
      switch (msg.type) {
        case 'agent_status': {
          const p = msg.payload as WSAgentStatusPayload;
          updateAgent(p.role, {
            status: p.status as 'running' | 'idle' | 'waiting' | 'error' | 'stopped',
            current_task_id: p.current_task_id,
            current_stage: p.current_stage,
          });
          break;
        }
        case 'activity': {
          const p = msg.payload as WSActivityPayload;
          addActivity(p);
          useNotificationStore.getState().bumpRefresh();
          break;
        }
        case 'gate_created': {
          const p = msg.payload as WSGatePayload;
          addNotification({
            id: `gate-${p.gate_id}`,
            type: 'gate_created',
            title: 'New Gate Approval',
            message: `Task ${p.task_id} stage ${p.stage} requires approval`,
            timestamp: msg.timestamp,
            read: false,
          });
          useNotificationStore.getState().bumpRefresh();
          break;
        }
        case 'gate_resolved': {
          const p = msg.payload as WSGatePayload;
          addNotification({
            id: `gate-resolved-${p.gate_id}`,
            type: 'gate_resolved',
            title: 'Gate Resolved',
            message: `Gate for task ${p.task_id} stage ${p.stage}: ${p.status}`,
            timestamp: msg.timestamp,
            read: false,
          });
          useNotificationStore.getState().bumpRefresh();
          break;
        }
        case 'pong':
          break;
        default:
          break;
      }
    }

    connect();

    return () => {
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
      if (heartbeatTimerRef.current) clearInterval(heartbeatTimerRef.current);
      wsRef.current?.close();
    };
  }, [updateAgent, addActivity, addNotification]);
}
