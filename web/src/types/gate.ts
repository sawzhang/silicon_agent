export interface Gate {
  id: string;
  gate_type: string;
  task_id: string;
  agent_role: string;
  content: Record<string, string> | null;
  status: 'pending' | 'approved' | 'rejected';
  reviewer: string | null;
  review_comment: string | null;
  created_at: string;
  reviewed_at: string | null;
}

export interface GateApproveRequest {
  reviewer?: string;
  comment?: string;
}

export interface GateRejectRequest {
  reviewer?: string;
  comment: string;
}
