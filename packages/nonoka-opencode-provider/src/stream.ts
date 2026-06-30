import type { LanguageModelV3StreamPart } from '@ai-sdk/provider';
import type { NonokaOutboundEvent } from './protocol.js';

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
        case 'session_init': {
          options.onSessionInit?.(event.session_id);
          break;
        }

        case 'text_delta': {
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

        case 'finish': {
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

        case 'error': {
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

function mapFinishReason(
  reason: 'stop' | 'error' | 'cancel',
): 'stop' | 'error' | 'length' | 'content-filter' | 'tool-calls' | 'other' {
  if (reason === 'cancel') {
    return 'other';
  }
  return reason;
}
