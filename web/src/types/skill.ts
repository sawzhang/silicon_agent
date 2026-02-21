export interface Skill {
  name: string;
  display_name: string;
  description: string;
  layer: 'foundation' | 'domain' | 'scenario';
  tags: string[];
  applicable_roles: string[];
  version: string;
  enabled: boolean;
  created_at: string;
  updated_at: string;
  config: Record<string, unknown>;
}

export interface SkillCreateRequest {
  name: string;
  display_name: string;
  description: string;
  layer: 'foundation' | 'domain' | 'scenario';
  tags?: string[];
  applicable_roles?: string[];
  config?: Record<string, unknown>;
}

export interface SkillUpdateRequest {
  display_name?: string;
  description?: string;
  tags?: string[];
  applicable_roles?: string[];
  enabled?: boolean;
  config?: Record<string, unknown>;
}
