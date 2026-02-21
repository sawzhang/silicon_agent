export const AGENT_ROLES = [
  { key: 'orchestrator', name: '编排官', color: '#722ed1' },
  { key: 'spec', name: '方案官', color: '#1677ff' },
  { key: 'coding', name: '研发官', color: '#52c41a' },
  { key: 'test', name: '测试官', color: '#faad14' },
  { key: 'review', name: '审计官', color: '#eb2f96' },
  { key: 'smoke', name: '巡检官', color: '#13c2c2' },
  { key: 'doc', name: '文档官', color: '#fa8c16' },
] as const;

export const ROLE_DISPLAY_NAMES: Record<string, string> = {
  orchestrator: '编排官',
  spec: '方案官',
  coding: '研发官',
  test: '测试官',
  review: '审计官',
  smoke: '巡检官',
  doc: '文档官',
};

export const STATUS_COLORS: Record<string, string> = {
  running: 'green',
  idle: 'blue',
  waiting: 'orange',
  error: 'red',
  stopped: 'default',
};

export const STAGE_NAMES = [
  { key: 'parse', name: '需求解析' },
  { key: 'spec', name: '方案设计' },
  { key: 'approve', name: '方案审批' },
  { key: 'code', name: '编码实现' },
  { key: 'test', name: '测试验证' },
  { key: 'review', name: '代码审计' },
  { key: 'smoke', name: '冒烟巡检' },
  { key: 'doc', name: '文档生成' },
  { key: 'signoff', name: '最终签收' },
] as const;

export const KPI_DEFINITIONS = [
  { name: 'tasks_completed', display_name: '任务完成数', target: 100, warning: 80, unit: '个' },
  { name: 'tokens_used', display_name: 'Token消耗', target: 1000000, warning: 800000, unit: 'tokens' },
  { name: 'total_cost', display_name: '总成本', target: 500, warning: 400, unit: '¥' },
  { name: 'savings_rate', display_name: '节省率', target: 60, warning: 40, unit: '%' },
  { name: 'avg_duration', display_name: '平均耗时', target: 30, warning: 45, unit: 'min' },
  { name: 'pass_rate', display_name: '一次通过率', target: 90, warning: 75, unit: '%' },
] as const;
