export interface StageDefinition {
  name: string;
  agent_role: string;
  order: number;
}

export interface GateDefinition {
  after_stage: string;
  type: string;
}

export interface TaskTemplate {
  id: string;
  name: string;
  display_name: string;
  description: string | null;
  stages: StageDefinition[];
  gates: GateDefinition[];
  is_builtin: boolean;
  created_at: string;
  updated_at: string;
}

export interface TemplateListResponse {
  items: TaskTemplate[];
  total: number;
}
