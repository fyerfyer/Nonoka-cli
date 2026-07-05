import type {
  LanguageModelV3,
  LanguageModelV3CallOptions,
  LanguageModelV3GenerateResult,
  LanguageModelV3StreamPart,
  SharedV3Warning,
} from '@ai-sdk/provider';
import { spawn, type ChildProcessWithoutNullStreams } from 'child_process';
import { Readable } from 'stream';
import {
  NONOKA_INBOUND_TYPES,
  NONOKA_MESSAGE_ROLES,
  encodeApprovalMessage,
  encodeChatRequest,
  type NonokaChatMessage,
  type NonokaChatRequest,
  type NonokaApprovalMessage,
} from './protocol.js';
import { createNonokaStreamTransformer } from './stream.js';
import fs from 'fs';

function providerLog(message: string) {
  try {
    fs.appendFileSync('/tmp/nonoka-provider.log', `${new Date().toISOString()} ${message}\n`);
  } catch {
    // ignore logging errors
  }
}

export interface NonokaLanguageModelConfig {
  provider: string;
  serverCommand: string[];
  cwd: string;
  configPath?: string;
  model?: string;
  env?: Record<string, string | undefined>;
}

export interface NonokaLanguageModelSettings {
  sessionId?: string;
}

export class NonokaLanguageModel implements LanguageModelV3 {
  readonly specificationVersion = 'v3' as const;
  readonly provider: string;
  readonly modelId: string;

  private readonly config: NonokaLanguageModelConfig;
  private readonly settings: NonokaLanguageModelSettings;
  private sessionId: string | undefined;

  constructor(
    modelId: string,
    settings: NonokaLanguageModelSettings,
    config: NonokaLanguageModelConfig,
  ) {
    this.modelId = modelId;
    this.provider = config.provider;
    this.config = config;
    this.settings = settings;
    this.sessionId = settings.sessionId;
  }

  get supportedUrls(): Record<string, RegExp[]> {
    return {};
  }

  async doGenerate(
    options: LanguageModelV3CallOptions,
  ): Promise<LanguageModelV3GenerateResult> {
    const { stream } = await this.doStream(options);

    const content: { type: 'text'; text: string }[] = [];
    let currentText = '';
    let finishReason: LanguageModelV3GenerateResult['finishReason'] = {
      unified: 'other',
      raw: undefined,
    };

    const reader = stream.getReader();
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      const part = value as LanguageModelV3StreamPart;
      if (part.type === 'text-delta') {
        currentText += part.delta;
      } else if (part.type === 'finish') {
        finishReason = part.finishReason;
      }
    }

    if (currentText) {
      content.push({ type: 'text', text: currentText });
    }

    return {
      content,
      finishReason,
      usage: {
        inputTokens: {
          total: undefined,
          noCache: undefined,
          cacheRead: undefined,
          cacheWrite: undefined,
        },
        outputTokens: {
          total: undefined,
          text: undefined,
          reasoning: undefined,
        },
      },
      warnings: [],
    };
  }

  async doStream(
    options: LanguageModelV3CallOptions,
  ): Promise<{
    stream: ReadableStream<LanguageModelV3StreamPart>;
    warnings: SharedV3Warning[];
  }> {
    const warnings: SharedV3Warning[] = [];

    providerLog('doStream start');
    const approvals = this.extractApprovalResponses(options);
    const child = this.spawnServer();
    providerLog(`spawned child pid=${child.pid}`);

    // If this request carries approval responses, send them before the chat
    // request so the backend can resume the paused turn.
    if (approvals.length > 0) {
      for (const approval of approvals) {
        const line = encodeApprovalMessage(approval) + '\n';
        await writeToStdin(child, line);
      }
    }

    const request = this.buildChatRequest(options);
    const requestLine = encodeChatRequest(request) + '\n';
    providerLog(`sending request: ${requestLine.trim()}`);
    await writeToStdin(child, requestLine);

    const rawStream = this.createOutputStream(child, options.abortSignal);

    // Wrap the raw stream so we can guarantee it closes as soon as the
    // server signals the turn is finished. OpenCode will not schedule the
    // next message until the ReadableStream reaches a closed state.
    const reader = rawStream.getReader();
    const controlledStream = new ReadableStream<LanguageModelV3StreamPart>({
      start: (controller) => {
        let closed = false;
        const closeOnce = () => {
          if (closed) return;
          closed = true;
          providerLog('controller.close called');
          try { controller.close(); } catch {}
          try { reader.cancel(); } catch {}
          this.killChild(child);
        };

        const pump = () => {
          reader.read().then(({ done, value }) => {
            if (done) {
              providerLog('raw stream done');
              closeOnce();
              return;
            }
            providerLog(`enqueuing part type=${value.type}`);
            controller.enqueue(value);
            if (value.type === 'finish' || value.type === 'error') {
              providerLog('terminal part seen, closing controlled stream');
              closeOnce();
              return;
            }
            pump();
          }).catch((err) => {
            providerLog(`raw stream error: ${err}`);
            controller.error(err);
            this.killChild(child);
          });
        };

        pump();
      },
      cancel: () => {
        providerLog('controlled stream cancelled');
        reader.cancel().catch(() => {});
        this.killChild(child);
      },
    });

    return { stream: controlledStream, warnings };
  }

  private buildChatRequest(options: LanguageModelV3CallOptions): NonokaChatRequest {
    const messages: NonokaChatMessage[] = [];
    const newSession = this.isNewConversation(options);

    if (newSession) {
      this.sessionId = undefined;
    }

    for (const message of options.prompt) {
      switch (message.role) {
        case NONOKA_MESSAGE_ROLES.system: {
          messages.push({
            role: NONOKA_MESSAGE_ROLES.system,
            content: message.content as string,
          });
          break;
        }
        case NONOKA_MESSAGE_ROLES.user: {
          const text = extractTextFromContent(message.content);
          messages.push({ role: NONOKA_MESSAGE_ROLES.user, content: text });
          break;
        }
        case NONOKA_MESSAGE_ROLES.assistant: {
          const text = extractTextFromContent(message.content);
          messages.push({ role: NONOKA_MESSAGE_ROLES.assistant, content: text });
          break;
        }
        case NONOKA_MESSAGE_ROLES.tool: {
          for (const part of message.content) {
            if (part.type === 'tool-result') {
              const outputText = extractToolOutputText(part.output);
              messages.push({
                role: NONOKA_MESSAGE_ROLES.tool,
                content: outputText,
                tool_call_id: part.toolCallId,
              });
            }
            // tool-approval-response parts are handled separately by
            // extractApprovalResponses and sent as standalone approval messages.
          }
          break;
        }
        default: {
          // Exhaustive check; ignore unknown roles.
          break;
        }
      }
    }

    return {
      type: NONOKA_INBOUND_TYPES.chat,
      messages,
      session_id: this.sessionId,
      new_session: newSession,
      cwd: this.config.cwd,
      model: this.config.model,
    };
  }

  private extractApprovalResponses(
    options: LanguageModelV3CallOptions,
  ): NonokaApprovalMessage[] {
    const approvals: NonokaApprovalMessage[] = [];

    for (const message of options.prompt) {
      if (message.role !== 'tool') continue;
      for (const part of message.content) {
        if (part.type !== 'tool-approval-response') continue;
        const decision: NonokaApprovalMessage = {
          type: NONOKA_INBOUND_TYPES.approval,
          id: part.approvalId,
          approved: part.approved,
        };
        // The AI SDK does not yet expose modified args in approval responses;
        // we reserve the field for future protocol extensions.
        approvals.push(decision);
      }
    }

    return approvals;
  }

  private isNewConversation(options: LanguageModelV3CallOptions): boolean {
    // OpenCode's /new resets the message history to system + user only.
    // If we see no prior assistant or tool messages, treat this as a fresh
    // nonoka session.
    for (const message of options.prompt) {
      if (message.role === 'assistant' || message.role === 'tool') {
        return false;
      }
    }
    return true;
  }

  private spawnServer(): ChildProcessWithoutNullStreams {
    const [command, ...baseArgs] = this.config.serverCommand;
    if (!command) {
      throw new Error('serverCommand must not be empty');
    }

    const args = [...baseArgs];
    if (this.config.configPath) {
      args.push('--config', this.config.configPath);
    }

    const env: Record<string, string | undefined> = {
      ...process.env,
      ...this.config.env,
    };

    return spawn(command, args, {
      cwd: this.config.cwd,
      env,
      stdio: ['pipe', 'pipe', 'pipe'],
    });
  }

  private killChild(child: ChildProcessWithoutNullStreams): void {
    providerLog(`killing child pid=${child.pid}`);
    try { child.stdin?.destroy(); } catch {}
    try { child.kill(); } catch {}
  }

  private createOutputStream(
    child: ChildProcessWithoutNullStreams,
    abortSignal?: AbortSignal,
  ): ReadableStream<LanguageModelV3StreamPart> {
    let cleanupDone = false;

    const cleanup = () => {
      if (cleanupDone) return;
      cleanupDone = true;
      try {
        child.stdin.destroy();
      } catch {}
      try {
        child.kill();
      } catch {}
    };

    if (abortSignal) {
      abortSignal.addEventListener('abort', cleanup, { once: true });
    }

    const transformer = createNonokaStreamTransformer({
      onSessionInit: (sessionId) => {
        this.sessionId = sessionId;
      },
    });

    const readable = Readable.toWeb(
      child.stdout,
    ) as ReadableStream<Uint8Array>;

    const composed = readable
      .pipeThrough(new TextDecoderStream() as unknown as TransformStream<Uint8Array, string>)
      .pipeThrough(createLineSplitter())
      .pipeThrough(transformer);

    // Propagate stderr to the parent process so diagnostics are visible.
    child.stderr.on('data', (data: Buffer) => {
      process.stderr.write(data);
    });

    child.on('error', (err) => {
      cleanup();
      throw err;
    });

    child.on('exit', () => {
      cleanup();
    });

    return composed;
  }
}

function writeToStdin(
  child: ChildProcessWithoutNullStreams,
  data: string,
): Promise<void> {
  return new Promise((resolve, reject) => {
    if (!child.stdin.writable) {
      resolve();
      return;
    }
    child.stdin.write(data, (err) => {
      if (err) reject(err);
      else resolve();
    });
  });
}

function createLineSplitter(): TransformStream<string, string> {
  let buffer = '';

  return new TransformStream<string, string>({
    transform(chunk, controller) {
      buffer += chunk;
      const lines = buffer.split('\n');
      buffer = lines.pop() ?? '';
      for (const line of lines) {
        controller.enqueue(line);
      }
    },
    flush(controller) {
      if (buffer) {
        controller.enqueue(buffer);
      }
    },
  });
}

function extractTextFromContent(
  content: string | Array<{ type: string; text?: string; toolName?: string; input?: unknown }>,
): string {
  if (typeof content === 'string') {
    return content;
  }
  return content
    .map((part) => {
      if (part.type === 'text') return part.text ?? '';
      if (part.type === 'tool-call') {
        return `<tool_call>${part.toolName ?? ''}(${JSON.stringify(part.input ?? {})})</tool_call>`;
      }
      return '';
    })
    .join('');
}

function extractToolOutputText(output: { type: string; value?: unknown; reason?: string; text?: string }): string {
  switch (output.type) {
    case 'text':
      return String(output.value ?? output.text ?? '');
    case 'json':
      return JSON.stringify(output.value);
    case 'execution-denied':
      return output.reason ?? 'Execution denied.';
    case 'error-text':
      return String(output.value ?? '');
    case 'error-json':
      return JSON.stringify(output.value);
    case 'content':
      return JSON.stringify(output.value);
    default:
      return JSON.stringify(output);
  }
}
