import type { LanguageModelV3StreamPart } from '@ai-sdk/provider';
import {
  NONOKA_FINISH_REASONS,
  NONOKA_BRIDGE_PROTOCOL_VERSION,
  NONOKA_REQUIRED_CAPABILITIES,
  NONOKA_OUTBOUND_TYPES,
  type NonokaOutboundEvent,
} from './protocol.js';
import fs from 'fs';
import { recordWorkspaceBefore } from './workspace.js';

const TIMELINE_PATH = process.env.NONOKA_TIMELINE_PATH;

function timelineLog(message: string) {
  if (!TIMELINE_PATH) return;
  try {
    fs.appendFileSync(TIMELINE_PATH, `${message}\n`);
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
    allowedToolNames?: Set<string>;
    cwd?: string;
    requireProtocolAck?: boolean;
    prepareToolArguments?: (
      toolCallId: string,
      toolName: string,
      args: Record<string, unknown>,
    ) => Record<string, unknown>;
  } = {},
): TransformStream<string, LanguageModelV3StreamPart> {
  let textBlockId: string | null = null;
  let textBlockStarted = false;
  // Buffer used to glue trailing whitespace to the next token so that OpenCode
  // does not drop leading spaces between text-delta chunks.
  let pendingText = '';
  // Tool calls that are not in OpenCode's native tool list are executed locally
  // by nonoka-cli. We must not forward them as tool-call parts to OpenCode;
  // instead we render their results as inline text.
  const suppressedToolCalls = new Set<string>();
  let protocolAcknowledged = !options.requireProtocolAck;

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

      if (!protocolAcknowledged && event.type !== NONOKA_OUTBOUND_TYPES.protocol_ack) {
        if (event.type === NONOKA_OUTBOUND_TYPES.error) {
          protocolAcknowledged = true;
        } else {
          controller.error(new Error('nonoka-cli did not acknowledge the bridge protocol contract'));
          return;
        }
      }

      switch (event.type) {
        case NONOKA_OUTBOUND_TYPES.protocol_ack: {
          const bridgeMajor = event.version.split('.', 1)[0];
          const expectedMajor = NONOKA_BRIDGE_PROTOCOL_VERSION.split('.', 1)[0];
          const missing = NONOKA_REQUIRED_CAPABILITIES.filter(
            (capability) => !event.capabilities.includes(capability),
          );
          if (bridgeMajor !== expectedMajor || missing.length > 0) {
            controller.error(new Error(
              `nonoka-cli protocol acknowledgement is incompatible (version=${event.version}, missing=${missing.join(',')})`,
            ));
            return;
          }
          protocolAcknowledged = true;
          break;
        }

        case NONOKA_OUTBOUND_TYPES.session_init: {
          options.onSessionInit?.(event.session_id);
          break;
        }

        case NONOKA_OUTBOUND_TYPES.tool_call_progress: {
          // Progress is deliberately not exposed as an AI SDK stream part:
          // it contains no model content and must not trigger a new turn.
          timelineLog(JSON.stringify({
            ts: new Date().toISOString(),
            source: 'bridge',
            type: 'tool_call_progress',
            toolCallIndex: event.tool_call_index,
            toolName: event.tool_name,
            argumentChars: event.argument_chars,
          }));
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
          const toolName = event.tool_name ?? '';
          const toolCallId = event.tool_call_id ?? '';
          const originalArgs = event.args && typeof event.args === 'object'
            ? event.args as Record<string, unknown>
            : {};
          const preparedArgs = options.prepareToolArguments?.(
            toolCallId, toolName, originalArgs,
          ) ?? originalArgs;
          if (options.cwd) recordWorkspaceBefore(
            options.cwd,
            toolCallId,
            toolName,
            originalArgs,
          );

          // Only forward tool calls for tools that OpenCode itself can execute.
          // MCP / skill tools executed locally by nonoka-cli are suppressed here
          // and their results are rendered as inline text instead.
          if (
            options.allowedToolNames &&
            toolName &&
            !options.allowedToolNames.has(toolName)
          ) {
            suppressedToolCalls.add(toolCallId);
            break;
          }

          // In deferred HITL mode the backend emits tool_call before the tool
          // has actually executed; it is waiting for an approval decision.
          // providerExecuted must be false so OpenCode renders the approval UI.
          const part: LanguageModelV3StreamPart & {
            metadata?: Record<string, unknown>;
          } = {
            type: 'tool-call' as const,
            toolCallId,
            toolName,
            input: JSON.stringify(preparedArgs),
            providerExecuted: false,
            dynamic: true,
            metadata: event.metadata,
          };
          logStreamPart(part);
          controller.enqueue(part as LanguageModelV3StreamPart);
          break;
        }

        case NONOKA_OUTBOUND_TYPES.tool_result: {
          flushPendingText(controller);
          const rawResult = event.result ?? event.content ?? '';
          const toolCallId = event.tool_call_id ?? '';

          // If the matching tool call was suppressed, render the locally
          // executed result as inline text rather than a tool-result part.
          if (suppressedToolCalls.has(toolCallId)) {
            suppressedToolCalls.delete(toolCallId);
            const text = typeof rawResult === 'string' ? rawResult : JSON.stringify(rawResult);
            const header = event.tool_name ? `[${event.tool_name} result]` : '[tool result]';
            pendingText += `\n\n${header}\n${text}`;
            flushPendingText(controller);
            break;
          }

          const part = {
            type: 'tool-result' as const,
            toolCallId,
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
          if (event.finish_reason !== NONOKA_FINISH_REASONS.tool_calls) {
            const usage = event.runtime?.usage;
            if (usage && typeof usage === 'object') {
              const state = usage as Record<string, unknown>;
              const focused = state.focused_verification_status;
              const full = state.full_verification_status;
              const modified = Number(state.effect_count ?? 0) > 0;
              if (focused && focused !== 'not_run') {
                pendingText += (
                  `\n\n[Nonoka status: modified=${modified ? 'yes' : 'no'}; `
                  + `focused_verification=${String(focused)}; full_suite=${String(full ?? 'not_run')}]`
                );
              }
            }
          }
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
      if (!protocolAcknowledged) {
        controller.error(new Error('nonoka-cli closed before acknowledging the bridge protocol contract'));
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
