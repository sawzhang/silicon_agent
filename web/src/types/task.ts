export interface StageOutputStructured {
  summary: string;
  status: 'pass' | 'fail' | 'partial';
  confidence: number;
  artifacts: string[];
  metadata: Record<string, unknown>;
  // Stage-specific fields (varies by stage)
  [key: string]: unknown;
}

export interface TaskStage {
  id: string;
  task_id: string;
  stage_name: string;
  agent_role: string;
  status: 'pending' | 'running' | 'completed' | 'failed' | 'skipped';
  started_at: string | null;
  completed_at: string | null;
  duration_seconds: number | null;
  tokens_used: number;
  turns_used: number;
  output_summary: string | null;
  error_message: string | null;
  // Phase 1.1: Structured output
  output_structured: StageOutputStructured | null;
  // Phase 1.2: Failure classification
  failure_category: string | null;
  // Phase 2.2: Self-assessment confidence
  self_assessment_score: number | null;
  // Phase 2.5: Per-stage retry count
  retry_count: number;
}

export interface Task {
  id: string;
  jira_id: string | null;
  title: string;
  description: string;
  status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled' | 'planning';
  created_at: string;
  completed_at: string | null;
  stages: TaskStage[];
  total_tokens: number;
  total_cost_rmb: number;
  template_id: string | null;
  project_id: string | null;
  template_name: string | null;
  project_name: string | null;
  target_branch: string | null;
  yunxiao_task_id: string | null;
  error_reason: string | null;
}

export interface YunxiaoTask {
  id: string;
  title: string;
  status: string;
}

export interface TaskListResponse {
  tasks: Task[];
  total: number;
  page: number;
  page_size: number;
}

export interface TaskCreateRequest {
  title: string;
  description: string;
  template_id?: string;
  project_id?: string;
  target_branch?: string;
  yunxiao_task_id?: string;
}

// --- PRD Decompose ---

export interface TaskDecomposeRequest {
  prd_text: string;
  project_id?: string;
  template_id?: string;
}

export interface DecomposedTask {
  title: string;
  description: string;
  priority: string;
}

export interface TaskDecomposeResponse {
  tasks: DecomposedTask[];
  summary: string;
  tokens_used: number;
}

// --- Batch Create ---

export interface BatchTaskItem {
  title: string;
  description?: string;
  template_id?: string;
  project_id?: string;
  target_branch?: string;
  yunxiao_task_id?: string;
}

export interface TaskBatchCreateRequest {
  tasks: BatchTaskItem[];
}

export interface TaskBatchCreateResponse {
  created: number;
  tasks: Task[];
}

/**
 * Request payload for retrying a task from a specific failed stage.
 */
export interface TaskRetryFromStageRequest {
  /** Target failed stage identifier belonging to the task. */
  stage_id: string;
}

/**
 * Request payload for batch retrying failed tasks.
 */
export interface TaskBatchRetryRequest {
  /** Task identifiers to retry in batch. */
  task_ids: string[];
}

/**
 * Per-task execution result returned by batch retry API.
 */
export interface TaskBatchRetryItem {
  /** Task identifier for this retry item. */
  task_id: string;
  /** Whether retry was accepted for this task. */
  success: boolean;
  /** Failure reason when retry is rejected. */
  reason?: string | null;
  /** Latest task snapshot when backend includes it. */
  task?: Task | null;
}

/**
 * Batch retry summary returned by backend.
 */
export interface TaskBatchRetryResponse {
  /** Number of tasks requested for retry. */
  total: number;
  /** Number of tasks successfully retried. */
  succeeded: number;
  /** Number of tasks failed to retry. */
  failed: number;
  /** Per-task retry result details. */
  items: TaskBatchRetryItem[];
}
