import React, { Suspense } from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import { Spin } from 'antd';
import BasicLayout from '@/layouts/BasicLayout';
import { useWebSocket } from '@/hooks/useWebSocket';

const Dashboard = React.lazy(() => import('@/pages/Dashboard/index'));
const Tasks = React.lazy(() => import('@/pages/Tasks/index'));
const TaskDetail = React.lazy(() => import('@/pages/Tasks/TaskDetail'));
const Gates = React.lazy(() => import('@/pages/Gates/index'));
const Skills = React.lazy(() => import('@/pages/Skills/index'));
const SkillDetail = React.lazy(() => import('@/pages/Skills/SkillDetail'));
const KPI = React.lazy(() => import('@/pages/KPI/index'));
const Audit = React.lazy(() => import('@/pages/Audit/index'));
const Config = React.lazy(() => import('@/pages/Config/index'));
const CircuitBreaker = React.lazy(() => import('@/pages/CircuitBreaker/index'));
const Projects = React.lazy(() => import('@/pages/Projects/index'));
const ROI = React.lazy(() => import('@/pages/ROI/index'));
const Cockpit = React.lazy(() => import('@/pages/Cockpit/index'));
const TaskLogs = React.lazy(() => import('@/pages/TaskLogs/index'));

const Loading = () => <Spin size="large" style={{ display: 'block', margin: '200px auto' }} />;

const App: React.FC = () => {
  useWebSocket();

  return (
    <Suspense fallback={<Loading />}>
      <Routes>
        <Route element={<BasicLayout />}>
          <Route path="/" element={<Navigate to="/dashboard" replace />} />
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/tasks" element={<Tasks />} />
          <Route path="/projects" element={<Projects />} />
          <Route path="/tasks/:id" element={<TaskDetail />} />
          <Route path="/gates" element={<Gates />} />
          <Route path="/skills" element={<Skills />} />
          <Route path="/skills/:name" element={<SkillDetail />} />
          <Route path="/cockpit" element={<Cockpit />} />
          <Route path="/kpi" element={<KPI />} />
          <Route path="/roi" element={<ROI />} />
          <Route path="/audit" element={<Audit />} />
          <Route path="/task-logs" element={<TaskLogs />} />
          <Route path="/config" element={<Config />} />
          <Route path="/circuit-breaker" element={<CircuitBreaker />} />
        </Route>
      </Routes>
    </Suspense>
  );
};

export default App;
