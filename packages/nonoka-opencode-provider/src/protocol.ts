/**
 * NDJSON protocol messages exchanged between the OpenCode provider and
 * the nonoka-cli --server backend.
 */

export const NONOKA_MESSAGE_ROLES = {
  system: 'system',
  user: 'user',
  assistant: 'assistant',
  tool: 'tool',
} as const;

export type NonokaMessageRole = keyof typeof NONOKA_MESSAGE_ROLES;

export interface NonokaChatToolCall {
  id: string;
  name: string;
  arguments: string;
}

export type ObservationCompleteness = 'complete' | 'partial' | 'unknown';

export interface NonokaExternalToolReceipt {
  result: unknown;
  exit_code?: number;
  host?: string;
  artifact_ref?: string;
  output_kind?: string;
  original_bytes?: number;
  truncated?: boolean;
  completeness: ObservationCompleteness;
  workspace?: Record<string, unknown>;
  effect?: Record<string, unknown>;
}

export interface NonokaChatMessage {
  role: NonokaMessageRole;
  content: string;
  tool_call_id?: string;
  tool_calls?: NonokaChatToolCall[];
  result?: unknown;
}

export interface ExternalToolDefinition {
  name: string;
  description: string;
  parameters: Record<string, unknown>;
}

export interface ExternalMCPServerDefinition {
  name: string;
  command: string;
  args?: string[];
  env?: Record<string, string>;
}

export interface ExternalSkillDefinition {
  name: string;
  package: string;
  version?: string;
  config?: Record<string, unknown>;
}

export const NONOKA_INBOUND_TYPES = {
  chat: 'chat',
  approval: 'approval',
  cancel: 'cancel',
} as const;

export type NonokaInboundEventType = keyof typeof NONOKA_INBOUND_TYPES;

export interface NonokaChatRequest {
  type: typeof NONOKA_INBOUND_TYPES.chat;
  purpose?: 'chat' | 'title';
  messages: NonokaChatMessage[];
  tools?: ExternalToolDefinition[];
  external_mcp_servers?: ExternalMCPServerDefinition[];
  external_skills?: ExternalSkillDefinition[];
  session_id?: string;
  new_session?: boolean;
  cwd: string;
  model?: string;
  temperature?: number;
  max_turns?: number;
  timeout_seconds?: number;
  wall_timeout_seconds?: number;
  tool_budget?: number;
  max_context_bytes?: number;
  max_external_result_bytes?: number;
  require_workspace_mutation?: boolean;
  require_observed_effect?: boolean;
  request_id?: string;
}

export interface NonokaApprovalMessage {
  type: typeof NONOKA_INBOUND_TYPES.approval;
  id: string;
  approved: boolean;
  modified_args?: Record<string, unknown>;
}

export interface NonokaCancelMessage {
  type: typeof NONOKA_INBOUND_TYPES.cancel;
  request_id?: string;
}

export type NonokaInboundMessage = NonokaChatRequest | NonokaApprovalMessage | NonokaCancelMessage;

export const NONOKA_OUTBOUND_TYPES = {
  session_init: 'session_init',
  text_delta: 'text_delta',
  tool_call: 'tool_call',
  tool_result: 'tool_result',
  tool_call_progress: 'tool_call_progress',
  approval_request: 'approval_request',
  debug: 'debug',
  finish: 'finish',
  error: 'error',
} as const;

export type NonokaOutboundEventType = keyof typeof NONOKA_OUTBOUND_TYPES;

export interface NonokaSessionInitEvent {
  type: typeof NONOKA_OUTBOUND_TYPES.session_init;
  session_id: string;
}

export interface NonokaTextDeltaEvent {
  type: typeof NONOKA_OUTBOUND_TYPES.text_delta;
  text: string;
}

export interface NonokaToolCallEvent {
  type: typeof NONOKA_OUTBOUND_TYPES.tool_call;
  tool_call_id: string;
  tool_name: string;
  args?: unknown;
  metadata?: Record<string, unknown>;
}

export interface NonokaToolResultEvent {
  type: typeof NONOKA_OUTBOUND_TYPES.tool_result;
  tool_call_id: string;
  tool_name: string;
  content: string;
  result?: unknown;
  is_error?: boolean;
}

export interface NonokaToolCallProgressEvent {
  type: typeof NONOKA_OUTBOUND_TYPES.tool_call_progress;
  tool_call_index: number;
  tool_name?: string;
  argument_chars: number;
}

export interface NonokaApprovalRequestEvent {
  type: typeof NONOKA_OUTBOUND_TYPES.approval_request;
  id: string;
  tool_call_id: string;
  tool_name: string;
  args?: unknown;
}

export interface NonokaDebugEvent {
  type: typeof NONOKA_OUTBOUND_TYPES.debug;
  level: 'info' | 'warning' | 'error';
  message: string;
  payload?: Record<string, unknown>;
}

export const NONOKA_FINISH_REASONS = {
  stop: 'stop',
  error: 'error',
  cancel: 'cancel',
  approval_required: 'approval_required',
  tool_calls: 'tool_calls',
} as const;

export type NonokaFinishReason = keyof typeof NONOKA_FINISH_REASONS;

export interface NonokaFinishEvent {
  type: typeof NONOKA_OUTBOUND_TYPES.finish;
  finish_reason: NonokaFinishReason;
  termination?: Record<string, unknown>;
  runtime?: Record<string, unknown>;
}

export interface NonokaErrorEvent {
  type: typeof NONOKA_OUTBOUND_TYPES.error;
  message: string;
  code?: string;
  retryable?: boolean;
  details?: Record<string, unknown>;
}

export type NonokaOutboundEvent =
  | NonokaSessionInitEvent
  | NonokaTextDeltaEvent
  | NonokaToolCallEvent
  | NonokaToolResultEvent
  | NonokaToolCallProgressEvent
  | NonokaApprovalRequestEvent
  | NonokaDebugEvent
  | NonokaFinishEvent
  | NonokaErrorEvent;

export function parseOutboundLine(line: string): NonokaOutboundEvent | null {
  const trimmed = line.trim();
  if (!trimmed) return null;
  return JSON.parse(trimmed) as NonokaOutboundEvent;
}

export function encodeChatRequest(req: NonokaChatRequest): string {
  return JSON.stringify(req);
}

export function encodeApprovalMessage(req: NonokaApprovalMessage): string {
  return JSON.stringify(req);
}

export function encodeCancelMessage(req: NonokaCancelMessage): string {
  return JSON.stringify(req);
}
