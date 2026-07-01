import type { LanguageModelV3StreamPart } from '@ai-sdk/provider';
import {
  NONOKA_FINISH_REASONS,
  NONOKA_OUTBOUND_TYPES,
  type NonokaOutboundEvent,
} from './protocol.js';

export function createNonokaStreamTransformer(
  options: {
    onSessionInit?: (sessionId: string) => void;
  } = {},
): TransformStream<string, LanguageModelV3StreamPart> {
  let textBlockId: string | null = null;
  let textBlockStarted = false;

  return new TransformStream<string, LanguageModelV3StreamPart>({
    transform(line, controller) {
      let event: NonokaOutboundEvent;
      try {
        event = JSON.parse(line.trim()) as NonokaOutboundEvent;
      } catch {
        // Ignore malformed lines; stderr lines may leak here.
        return;
      }

      switch (event.type) {
        case NONOKA_OUTBOUND_TYPES.session_init: {
          options.onSessionInit?.(event.session_id);
          break;
        }

        case NONOKA_OUTBOUND_TYPES.text_delta: {
          if (textBlockId === null) {
            textBlockId = generateId();
            textBlockStarted = true;
            controller.enqueue({
              type: 'text-start',
              id: textBlockId,
            });
          }

          controller.enqueue({
            type: 'text-delta',
            id: textBlockId,
            delta: event.text,
          });
          break;
        }

        case NONOKA_OUTBOUND_TYPES.tool_call: {
          controller.enqueue({
            type: 'tool-call',
            toolCallId: event.tool_call_id,
            toolName: event.tool_name,
            input: JSON.stringify(event.args ?? {}),
            providerExecuted: true,
            dynamic: true,
          });
          break;
        }

        case NONOKA_OUTBOUND_TYPES.tool_result: {
          const rawResult = event.result ?? event.content ?? '';
          controller.enqueue({
            type: 'tool-result',
            toolCallId: event.tool_call_id,
            toolName: event.tool_name,
            result: rawResult as any,
            isError: event.is_error ?? false,
            dynamic: true,
          });
          break;
        }

        case NONOKA_OUTBOUND_TYPES.approval_request: {
          controller.enqueue({
            type: 'tool-approval-request',
            approvalId: event.id,
            toolCallId: event.tool_call_id,
          });
          break;
        }

        case NONOKA_OUTBOUND_TYPES.finish: {
          if (textBlockStarted && textBlockId !== null) {
            controller.enqueue({
              type: 'text-end',
              id: textBlockId,
            });
            textBlockStarted = false;
            textBlockId = null;
          }

          const unified = mapFinishReason(event.finish_reason);
          controller.enqueue({
            type: 'finish',
            finishReason: {
              unified,
              raw: event.finish_reason,
            },
            usage: {
              inputTokens: { total: undefined, noCache: undefined, cacheRead: undefined, cacheWrite: undefined },
              outputTokens: { total: undefined, text: undefined, reasoning: undefined },
            },
          });
          break;
        }

        case NONOKA_OUTBOUND_TYPES.error: {
          if (textBlockStarted && textBlockId !== null) {
            controller.enqueue({
              type: 'text-end',
              id: textBlockId,
            });
            textBlockStarted = false;
            textBlockId = null;
          }

          controller.enqueue({
            type: 'error',
            error: event.message,
          });
          break;
        }

        default: {
          // Unknown event type; ignore.
          break;
        }
      }
    },

    flush(controller) {
      if (textBlockStarted && textBlockId !== null) {
        controller.enqueue({
          type: 'text-end',
          id: textBlockId,
        });
      }
    },
  });
}

function generateId(): string {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 11)}`;
}

const UNIFIED_FINISH_REASONS = {
  stop: 'stop',
  error: 'error',
  cancel: 'other',
  approval_required: 'tool-calls',
  tool_calls: 'tool-calls',
} as const;

function mapFinishReason(
  reason: keyof typeof UNIFIED_FINISH_REASONS,
): 'stop' | 'error' | 'length' | 'content-filter' | 'tool-calls' | 'other' {
  return UNIFIED_FINISH_REASONS[reason];
}

// Keep the NONOKA_FINISH_REASONS export used so TS doesn't complain about
// unused imports when consumers use the type directly.
export { NONOKA_FINISH_REASONS };
