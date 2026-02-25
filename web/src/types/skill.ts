export interface Skill {
  id: string;
  name: string;
  display_name: string;
  description: string | null;
  layer: string;
  tags: string[] | null;
  applicable_roles: string[] | null;
  version: string;
  status: string;
  content: string | null;
  git_path: string | null;
  created_at: string;
  updated_at: string;
}

export interface SkillVersion {
  version: string;
  created_at: string;
  current: boolean;
  change_summary?: string;
}

export interface SkillCreateRequest {
  name: string;
  display_name: string;
  description?: string;
  layer?: string;
  tags?: string[];
  applicable_roles?: string[];
  content?: string;
  git_path?: string;
}

export interface SkillUpdateRequest {
  display_name?: string;
  description?: string;
  layer?: string;
  tags?: string[] | null;
  applicable_roles?: string[];
  content?: string | null;
  git_path?: string | null;
  version?: string;
}

export interface SkillListResponse {
  items: Skill[];
  total: number;
  page: number;
  page_size: number;
}

export interface SkillVersionListResponse {
  name: string;
  versions: SkillVersion[];
}

export interface SkillStatsResponse {
  total: number;
  by_layer: Record<string, number>;
  by_status: Record<string, number>;
}

export interface SkillSyncResponse {
  synced: number;
  created: number;
  updated: number;
  details: Record<string, string>;
}
