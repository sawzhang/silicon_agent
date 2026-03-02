export interface Integration {
  id: string;
  project_id: string;
  provider: 'github' | 'jira' | 'gitlab';
  webhook_secret: string;
  access_token: string | null;
  extra_config: Record<string, unknown> | null;
  enabled: boolean;
  webhook_url: string;
  created_at: string;
  updated_at: string;
}

export interface IntegrationCreateRequest {
  provider: string;
  access_token?: string;
  extra_config?: Record<string, unknown>;
  enabled?: boolean;
}

export interface IntegrationUpdateRequest {
  access_token?: string;
  extra_config?: Record<string, unknown>;
  enabled?: boolean;
}
