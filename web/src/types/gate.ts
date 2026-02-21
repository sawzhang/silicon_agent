export interface Gate {
  id: string;
  task_id: string;
  stage: string;
  gate_type: 'human_approval' | 'auto_check' | 'quality_gate';
  status: 'pending' | 'approved' | 'rejected' | 'timeout';
  content: string;
  summary: string;
  requested_at: string;
  resolved_at: string | null;
  resolved_by: string | null;
  comment: string | null;
  timeout_seconds: number;
}

export interface GateApproveRequest {
  comment?: string;
}

export interface GateRejectRequest {
  comment: string;
  reason: string;
}
