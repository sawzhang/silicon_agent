export interface Project {
  id: string;
  name: string;
  display_name: string;
  repo_url: string | null;
  branch: string;
  description: string | null;
  status: 'active' | 'archived';
  tech_stack: string[] | null;
  repo_tree: string | null;
  last_synced_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ProjectListResponse {
  items: Project[];
  total: number;
}

export interface ProjectCreateRequest {
  name: string;
  display_name: string;
  repo_url?: string;
  branch?: string;
  description?: string;
}

export interface ProjectUpdateRequest {
  display_name?: string;
  repo_url?: string;
  branch?: string;
  description?: string;
  status?: string;
}

export interface ProjectSyncResponse {
  tech_stack: string[];
  tree_depth: number;
  readme_length: number;
  synced_at: string;
}
