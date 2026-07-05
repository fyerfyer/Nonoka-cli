import type { LanguageModelV3StreamPart } from '@ai-sdk/provider';
import {
  NONOKA_FINISH_REASONS,
  NONOKA_OUTBOUND_TYPES,
  type NonokaOutboundEvent,
} from './protocol.js';
import fs from 'fs';

function timelineLog(message: string) {
  try {
    fs.appendFileSync('/tmp/nonoka-tui-timeline.ndjson', `${new Date().toISOString()} ${message}\n`);
  } catch {
    // ignore logging errors
  }
}

function logStreamPart(part: LanguageModelV3StreamPart) {
  const base = { ts: new Date().toISOString(), source: 'provider', type: part.type };
  if (part.type === 'text-delta') {
    timelineLog(
      JSON.stringify({
        ...base,
        len: part.delta.length,
        hasNewline: part.delta.includes('\n'),
        preview: part.delta.slice(0, 80).replace(/\n/g, '\\n'),
      }),
    );
  } else if (
    part.type === 'tool-call' ||
    part.type === 'tool-result' ||
    part.type === 'tool-approval-request'
  ) {
    timelineLog(
      JSON.stringify({
        ...base,
        toolName: (part as any).toolName || (part as any).tool_name,
        toolCallId: (part as any).toolCallId || (part as any).tool_call_id,
      }),
    );
  } else {
    timelineLog(JSON.stringify(base));
  }
}

export function createNonokaStreamTransformer(
  options: {
    onSessionInit?: (sessionId: string) => void;
    onFinish?: () => void;
  } = {},
): TransformStream<string, LanguageModelV3StreamPart> {
  let textBlockId: string | null = null;
  let textBlockStarted = false;
  // Buffer used to glue trailing whitespace to the next token so that OpenCode
  // does not drop leading spaces between text-delta chunks.
  let pendingText = '';

  function startTextBlock(controller: TransformStreamDefaultController<LanguageModelV3StreamPart>) {
    if (textBlockId === null) {
      textBlockId = generateId();
      textBlockStarted = true;
      const startPart = { type: 'text-start' as const, id: textBlockId };
      logStreamPart(startPart);
      controller.enqueue(startPart);
    }
  }

  function flushPendingText(controller: TransformStreamDefaultController<LanguageModelV3StreamPart>) {
    if (!pendingText) return;
    startTextBlock(controller);
    const part = { type: 'text-delta' as const, id: textBlockId!, delta: pendingText };
    logStreamPart(part);
    controller.enqueue(part);
    pendingText = '';
  }

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
          const text = event.text ?? '';
          if (!text) break;
          pendingText += text;
          // Keep buffering while the pending text ends with whitespace; flush
          // as soon as a non-whitespace character arrives so spaces are sent
          // attached to the word that follows them.
          if (!/\s$/.test(pendingText)) {
            flushPendingText(controller);
          }
          break;
        }

        case NONOKA_OUTBOUND_TYPES.tool_call: {
          flushPendingText(controller);
          const part = {
            type: 'tool-call' as const,
            toolCallId: event.tool_call_id,
            toolName: event.tool_name,
            input: JSON.stringify(event.args ?? {}),
            providerExecuted: true,
            dynamic: true,
          };
          logStreamPart(part);
          controller.enqueue(part);
          break;
        }

        case NONOKA_OUTBOUND_TYPES.tool_result: {
          flushPendingText(controller);
          const rawResult = event.result ?? event.content ?? '';
          const part = {
            type: 'tool-result' as const,
            toolCallId: event.tool_call_id,
            toolName: event.tool_name,
            result: rawResult as any,
            isError: event.is_error ?? false,
            dynamic: true,
          };
          logStreamPart(part);
          controller.enqueue(part);
          break;
        }

        case NONOKA_OUTBOUND_TYPES.approval_request: {
          flushPendingText(controller);
          const approvalId = event.tool_call_id || event.id || 'unknown';
          const part = {
            type: 'tool-approval-request' as const,
            approvalId,
            toolCallId: event.tool_call_id,
            isAutomatic: false,
          };
          logStreamPart(part);
          controller.enqueue(part);
          break;
        }

        case NONOKA_OUTBOUND_TYPES.finish: {
          flushPendingText(controller);
          if (textBlockStarted && textBlockId !== null) {
            const endPart = { type: 'text-end' as const, id: textBlockId };
            logStreamPart(endPart);
            controller.enqueue(endPart);
            textBlockStarted = false;
            textBlockId = null;
          }

          const unified = mapFinishReason(event.finish_reason);
          const part = {
            type: 'finish' as const,
            finishReason: {
              unified,
              raw: event.finish_reason,
            },
            usage: {
              inputTokens: { total: undefined, noCache: undefined, cacheRead: undefined, cacheWrite: undefined },
              outputTokens: { total: undefined, text: undefined, reasoning: undefined },
            },
          };
          logStreamPart(part);
          controller.enqueue(part);
          // The server processed one complete turn; signal the caller to
          // terminate the child process so OpenCode can schedule the next turn.
          options.onFinish?.();
          break;
        }

        case NONOKA_OUTBOUND_TYPES.error: {
          flushPendingText(controller);
          if (textBlockStarted && textBlockId !== null) {
            const endPart = { type: 'text-end' as const, id: textBlockId };
            logStreamPart(endPart);
            controller.enqueue(endPart);
            textBlockStarted = false;
            textBlockId = null;
          }

          const part = { type: 'error' as const, error: event.message };
          logStreamPart(part);
          controller.enqueue(part);
          break;
        }

        default: {
          // Unknown event type; ignore.
          break;
        }
      }
    },

    flush(controller) {
      flushPendingText(controller);
      if (textBlockStarted && textBlockId !== null) {
        const endPart = { type: 'text-end' as const, id: textBlockId };
        logStreamPart(endPart);
        controller.enqueue(endPart);
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
