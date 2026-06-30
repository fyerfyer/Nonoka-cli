/**
 * NDJSON protocol messages exchanged between the OpenCode provider and
 * the nonoka-cli --server backend.
 */

export type NonokaMessageRole = 'system' | 'user' | 'assistant' | 'tool';

export interface NonokaChatMessage {
  role: NonokaMessageRole;
  content: string;
  tool_call_id?: string;
}

export interface NonokaChatRequest {
  type: 'chat';
  messages: NonokaChatMessage[];
  session_id?: string;
  cwd: string;
  model?: string;
}

export type NonokaInboundMessage = NonokaChatRequest;

export interface NonokaSessionInitEvent {
  type: 'session_init';
  session_id: string;
}

export interface NonokaTextDeltaEvent {
  type: 'text_delta';
  text: string;
}

export interface NonokaFinishEvent {
  type: 'finish';
  finish_reason: 'stop' | 'error' | 'cancel';
}

export interface NonokaErrorEvent {
  type: 'error';
  message: string;
}

export type NonokaOutboundEvent =
  | NonokaSessionInitEvent
  | NonokaTextDeltaEvent
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
