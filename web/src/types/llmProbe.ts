/**
 * Query parameters for LLM quick probe endpoint.
 */
export interface LLMProbeRequest {
  /** Optional model override for this probe request. */
  model?: string;
  /** Optional timeout in milliseconds; backend validates range. */
  timeout_ms?: number;
}

/**
 * Normalized response payload for LLM quick liveness checks.
 */
export interface LLMProbeResponse {
  /** Whether probe succeeded at model-channel level. */
  ok: boolean;
  /** LLM provider identifier. */
  provider: string;
  /** Provider base URL used by backend client. */
  base_url: string;
  /** Model requested by caller. */
  requested_model?: string | null;
  /** Model resolved by provider/backend runtime. */
  resolved_model?: string | null;
  /** End-to-end probe latency in milliseconds. */
  latency_ms: number;
  /** Prompt token usage for this probe call. */
  input_tokens: number;
  /** Completion token usage for this probe call. */
  output_tokens: number;
  /** Total token usage for this probe call. */
  total_tokens: number;
  /** Normalized probe error code when ok=false. */
  error_code?: string | null;
  /** Human-readable error message when ok=false. */
  error_message?: string | null;
  /** Probe execution timestamp in ISO string format. */
  checked_at: string;
}

export interface LLMConfigResponse {
  api_key_set: boolean;
  api_key_masked: string;
  base_url: string;
  model: string;
  timeout: number;
  role_model_map: Record<string, string>;
}

export interface LLMConfigUpdateRequest {
  api_key?: string;
  base_url?: string;
  model?: string;
  timeout?: number;
}
