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
  SettingOutlined,
  ThunderboltOutlined,
  ProjectOutlined,
} from '@ant-design/icons';
import { Badge } from 'antd';
import { useNotificationStore } from '@/stores/notificationStore';

const menuRoutes = {
  routes: [
    { path: '/dashboard', name: '集群总览', icon: <DashboardOutlined /> },
    { path: '/tasks', name: '任务管线', icon: <UnorderedListOutlined /> },
    { path: '/projects', name: '项目管理', icon: <ProjectOutlined /> },
    { path: '/gates', name: '审批中心', icon: <AuditOutlined /> },
    { path: '/skills', name: 'Skills管理', icon: <ToolOutlined /> },
    { path: '/kpi', name: 'KPI监控', icon: <BarChartOutlined /> },
    { path: '/audit', name: '审计日志', icon: <FileSearchOutlined /> },
    { path: '/config', name: 'Agent配置', icon: <SettingOutlined /> },
    { path: '/circuit-breaker', name: '止损控制', icon: <ThunderboltOutlined /> },
  ],
};

const BasicLayout: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const unreadCount = useNotificationStore((s) => s.unreadCount());

  return (
    <ProLayout
      title="SITC Agent Platform"
      layout="mix"
      fixSiderbar
      location={{ pathname: location.pathname }}
      route={menuRoutes}
      menuItemRender={(item, dom) => (
        <a onClick={() => item.path && navigate(item.path)}>
          {item.path === '/gates' && unreadCount > 0 ? (
            <Badge count={unreadCount} size="small" offset={[8, 0]}>{dom}</Badge>
          ) : (
            dom
          )}
        </a>
      )}
      contentStyle={{ padding: 24 }}
    >
      <Outlet />
    </ProLayout>
  );
};

export default BasicLayout;
