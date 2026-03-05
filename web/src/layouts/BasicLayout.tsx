import React from 'react';
import { Outlet, useNavigate, useLocation } from 'react-router-dom';
import { ProLayout } from '@ant-design/pro-components';
import {
  DashboardOutlined,
  UnorderedListOutlined,
  AuditOutlined,
  ToolOutlined,
  BarChartOutlined,
  FileSearchOutlined,
  FileTextOutlined,
  SettingOutlined,
  ThunderboltOutlined,
  ProjectOutlined,
  ControlOutlined,
  FundOutlined,
  ApiOutlined,
} from '@ant-design/icons';
import { Badge, Space, Tag, Tooltip } from 'antd';
import { useGateList } from '@/hooks/useGates';
import { useWSConnectionStore } from '@/stores/wsConnectionStore';

const menuRoutes = {
  routes: [
    { path: '/dashboard', name: '集群总览', icon: <DashboardOutlined /> },
    { path: '/cockpit', name: '指挥台', icon: <ControlOutlined /> },
    { path: '/tasks', name: '任务管线', icon: <UnorderedListOutlined /> },
    { path: '/projects', name: '项目管理', icon: <ProjectOutlined /> },
    { path: '/gates', name: '审批中心', icon: <AuditOutlined /> },
    { path: '/skills', name: 'Skills管理', icon: <ToolOutlined /> },
    { path: '/kpi', name: 'KPI监控', icon: <BarChartOutlined /> },
    { path: '/roi', name: 'ROI 分析', icon: <FundOutlined /> },
    { path: '/audit', name: '审计日志', icon: <FileSearchOutlined /> },
    { path: '/task-logs', name: '任务日志', icon: <FileTextOutlined /> },
    { path: '/config', name: 'Agent配置', icon: <SettingOutlined /> },
    { path: '/circuit-breaker', name: '止损控制', icon: <ThunderboltOutlined /> },
    { path: '/api-docs', name: 'API 文档', icon: <ApiOutlined /> },
  ],
};

const WS_STATUS_CONFIG = {
  connected: { color: 'success' as const, label: '已连接' },
  disconnected: { color: 'error' as const, label: '已断开' },
  reconnecting: { color: 'warning' as const, label: '重连中...' },
};

const BasicLayout: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const { data: pendingGates } = useGateList({ status: 'pending' });
  const pendingGateCount = pendingGates?.length ?? 0;
  const wsStatus = useWSConnectionStore((s) => s.status);
  const wsRetryCount = useWSConnectionStore((s) => s.retryCount);
  const statusCfg = WS_STATUS_CONFIG[wsStatus];

  return (
    <ProLayout
      title="Silicon Agent"
      layout="mix"
      fixSiderbar
      location={{ pathname: location.pathname }}
      route={menuRoutes}
      menuItemRender={(item, dom) => {
        if (item.path === '/api-docs') {
          return (
            <a href="/docs" target="_blank" rel="noopener noreferrer">
              {dom}
            </a>
          );
        }
        return (
          <a onClick={() => item.path && navigate(item.path)}>
            {item.path === '/gates' && pendingGateCount > 0 ? (
              <Badge count={pendingGateCount} size="small" offset={[8, 0]}>{dom}</Badge>
            ) : (
              dom
            )}
          </a>
        );
      }}
      actionsRender={() => [
        <Space key="ws-status" size={4}>
          <Tooltip title={wsRetryCount > 0 ? `重试次数: ${wsRetryCount}` : '实时连接'}>
            <Tag
              icon={<ApiOutlined />}
              color={statusCfg.color}
              style={{ marginInlineEnd: 0 }}
            >
              {statusCfg.label}
            </Tag>
          </Tooltip>
        </Space>,
      ]}
      contentStyle={{ padding: 24 }}
    >
      <Outlet />
    </ProLayout>
  );
};

export default BasicLayout;
