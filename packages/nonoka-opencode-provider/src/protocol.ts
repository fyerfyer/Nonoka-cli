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

export interface NonokaChatMessage {
  role: NonokaMessageRole;
  content: string;
  tool_call_id?: string;
  tool_calls?: NonokaChatToolCall[];
}

export interface ExternalToolDefinition {
  name: string;
  description: string;
  parameters: Record<string, unknown>;
}

export const NONOKA_INBOUND_TYPES = {
  chat: 'chat',
  approval: 'approval',
} as const;

export type NonokaInboundEventType = keyof typeof NONOKA_INBOUND_TYPES;

export interface NonokaChatRequest {
  type: typeof NONOKA_INBOUND_TYPES.chat;
  messages: NonokaChatMessage[];
  tools?: ExternalToolDefinition[];
  session_id?: string;
  new_session?: boolean;
  cwd: string;
  model?: string;
  request_id?: string;
}

export interface NonokaApprovalMessage {
  type: typeof NONOKA_INBOUND_TYPES.approval;
  id: string;
  approved: boolean;
  modified_args?: Record<string, unknown>;
}

export type NonokaInboundMessage = NonokaChatRequest | NonokaApprovalMessage;

export const NONOKA_OUTBOUND_TYPES = {
  session_init: 'session_init',
  text_delta: 'text_delta',
  tool_call: 'tool_call',
  tool_result: 'tool_result',
  approval_request: 'approval_request',
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
}

export interface NonokaToolResultEvent {
  type: typeof NONOKA_OUTBOUND_TYPES.tool_result;
  tool_call_id: string;
  tool_name: string;
  content: string;
  result?: unknown;
  is_error?: boolean;
}

export interface NonokaApprovalRequestEvent {
  type: typeof NONOKA_OUTBOUND_TYPES.approval_request;
  id: string;
  tool_call_id: string;
  tool_name: string;
  args?: unknown;
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
}

export interface NonokaErrorEvent {
  type: typeof NONOKA_OUTBOUND_TYPES.error;
  message: string;
}

export type NonokaOutboundEvent =
  | NonokaSessionInitEvent
  | NonokaTextDeltaEvent
  | NonokaToolCallEvent
  | NonokaToolResultEvent
  | NonokaApprovalRequestEvent
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
