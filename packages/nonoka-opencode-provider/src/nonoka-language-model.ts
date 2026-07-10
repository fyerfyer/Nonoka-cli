import type {
  LanguageModelV3,
  LanguageModelV3CallOptions,
  LanguageModelV3GenerateResult,
  LanguageModelV3StreamPart,
  SharedV3Warning,
} from '@ai-sdk/provider';
import { spawn, type ChildProcessWithoutNullStreams } from 'child_process';
import { createHash } from 'crypto';
import { existsSync, readFileSync, writeFileSync } from 'fs';
import path from 'path';
import { Readable } from 'stream';
import {
  NONOKA_INBOUND_TYPES,
  NONOKA_MESSAGE_ROLES,
  encodeChatRequest,
  type NonokaChatMessage,
  type NonokaChatRequest,
  type NonokaChatToolCall,
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

function generateId(): string {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 11)}`;
}

function generateRequestId(): string {
  return `req-${generateId()}`;
}

const TITLE_PROMPT_SENTINEL = 'Generate a title for this conversation';

function cwdHash(cwd: string): string {
  return createHash('sha256').update(path.resolve(cwd)).digest('hex').slice(0, 16);
}

export function getChatSessionIdFile(cwd: string): string {
  return path.join('/tmp', `nonoka-chat-${cwdHash(cwd)}.id`);
}

export function loadChatSessionId(cwd: string): string | undefined {
  try {
    const file = getChatSessionIdFile(cwd);
    if (!existsSync(file)) return undefined;
    const id = readFileSync(file, 'utf-8').trim();
    return id || undefined;
  } catch {
    return undefined;
  }
}

export function saveChatSessionId(cwd: string, sessionId: string | undefined): void {
  try {
    const file = getChatSessionIdFile(cwd);
    if (!sessionId) {
      return;
    }
    writeFileSync(file, sessionId, 'utf-8');
  } catch {
    // ignore persistence errors
  }
}

function getServerStderrLogPath(cwd: string): string {
  return path.join('/tmp', `nonoka-server-${cwdHash(cwd)}.log`);
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
  private chatSessionId: string | undefined;
  private titleSessionId: string | undefined;

  constructor(
    modelId: string,
    settings: NonokaLanguageModelSettings,
    config: NonokaLanguageModelConfig,
  ) {
    this.modelId = modelId;
    this.provider = config.provider;
    this.config = config;
    this.settings = settings;
    this.chatSessionId = settings.sessionId ?? loadChatSessionId(config.cwd);
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

    const isTitle = this.isTitleGeneration(options);
    providerLog(`doStream start isTitle=${isTitle}`);
    providerLog(`options.tools count=${options.tools?.length ?? 0} names=${JSON.stringify(options.tools?.map((t: any) => t.name))}`);
    const child = this.spawnServer();
    providerLog(`spawned child pid=${child.pid}`);

    const request = this.buildChatRequest(options, isTitle);
    const requestLine = encodeChatRequest(request) + '\n';
    providerLog(`sending request: ${requestLine.trim()}`);
    await writeToStdin(child, requestLine);

    const rawStream = this.createOutputStream(child, isTitle, options.abortSignal);

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

  private isTitleGeneration(options: LanguageModelV3CallOptions): boolean {
    for (const message of options.prompt) {
      if (message.role === NONOKA_MESSAGE_ROLES.user) {
        const text = extractTextFromContent(message.content);
        if (text.includes(TITLE_PROMPT_SENTINEL)) {
          return true;
        }
      }
    }
    return false;
  }

  private buildChatRequest(
    options: LanguageModelV3CallOptions,
    isTitle: boolean,
  ): NonokaChatRequest {
    const messages = this.convertPromptMessages(options.prompt, isTitle);

    let sessionId: string | undefined;
    let newSession = false;

    if (isTitle) {
      // Title generation never reads or writes the chat session.
      this.titleSessionId = generateId();
      sessionId = this.titleSessionId;
      newSession = true;
    } else {
      newSession = this.isNewConversation(options);
      if (newSession) {
        this.chatSessionId = undefined;
      }
      sessionId = this.chatSessionId;
    }

    const tools = options.tools
      ?.filter((tool): tool is { type: 'function'; name: string; description?: string; inputSchema: Record<string, unknown> } => tool.type === 'function')
      .map((tool) => ({
        name: tool.name,
        description: tool.description ?? '',
        parameters: tool.inputSchema,
      }));
    providerLog(`buildChatRequest isTitle=${isTitle} tools count=${tools?.length ?? 0} names=${JSON.stringify(tools?.map((t) => t.name))}`);
    return {
      type: NONOKA_INBOUND_TYPES.chat,
      messages,
      tools,
      session_id: sessionId,
      new_session: newSession,
      cwd: this.config.cwd,
      model: this.config.model,
      request_id: generateRequestId(),
    };
  }

  private convertPromptMessages(
    prompt: LanguageModelV3CallOptions['prompt'],
    isTitle: boolean,
  ): NonokaChatMessage[] {
    const messages: NonokaChatMessage[] = [];

    for (const message of prompt) {
      switch (message.role) {
        case NONOKA_MESSAGE_ROLES.system: {
          messages.push({
            role: NONOKA_MESSAGE_ROLES.system,
            content: extractTextFromContent(message.content),
          });
          break;
        }
        case NONOKA_MESSAGE_ROLES.user: {
          const text = extractTextFromContent(message.content);
          if (isTitle && messages.length > 0) {
            const last = messages[messages.length - 1];
            if (last && last.role === NONOKA_MESSAGE_ROLES.user) {
              // OpenCode title generator sends two consecutive user messages.
              // Merge them to satisfy strict chat templates.
              last.content = `${last.content}\n\n${text}`;
              break;
            }
          }
          messages.push({ role: NONOKA_MESSAGE_ROLES.user, content: text });
          break;
        }
        case NONOKA_MESSAGE_ROLES.assistant: {
          const text = extractTextFromContent(message.content);
          const toolCalls = extractToolCallsFromContent(message.content);
          messages.push({
            role: NONOKA_MESSAGE_ROLES.assistant,
            content: text,
            tool_calls: toolCalls,
          });
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
            } else if (part.type === 'tool-approval-response') {
              // Encode the approval decision as a JSON part list inside a
              // role="tool" message. The nonoka-cli bridge extracts these
              // parts in ChatRequestHandler._extract_approvals() and routes
              // them to Orchestrator.resume_approval().
              // OpenCode identifies approvals by approvalId; our bridge emits
              // approvalId equal to the nonoka tool_call_id, so we can use it
              // as the resume key.
              const toolCallId = part.approvalId;
              messages.push({
                role: NONOKA_MESSAGE_ROLES.tool,
                content: JSON.stringify([
                  {
                    type: 'tool-approval-response',
                    toolCallId,
                    approved: part.approved,
                  },
                ]),
                tool_call_id: toolCallId,
              });
            }
          }
          break;
        }
        default: {
          // Exhaustive check; ignore unknown roles.
          break;
        }
      }
    }

    return messages;
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

    const child = spawn(command, args, {
      cwd: this.config.cwd,
      env,
      stdio: ['pipe', 'pipe', 'pipe'],
    });

    // Redirect server stderr to a file so it does not leak into OpenCode's TUI.
    const stderrLogPath = getServerStderrLogPath(this.config.cwd);
    try {
      const stderrStream = fs.createWriteStream(stderrLogPath, { flags: 'a' });
      child.stderr.pipe(stderrStream);
    } catch (err) {
      providerLog(`failed to redirect stderr to ${stderrLogPath}: ${err}`);
    }

    return child;
  }

  private killChild(child: ChildProcessWithoutNullStreams): void {
    providerLog(`killing child pid=${child.pid}`);
    try { child.stdin?.destroy(); } catch {}
    try { child.kill(); } catch {}
  }

  private createOutputStream(
    child: ChildProcessWithoutNullStreams,
    isTitle: boolean,
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
        // Title sessions are ephemeral; never persist them as the chat session.
        if (isTitle) {
          this.titleSessionId = sessionId;
          return;
        }
        this.chatSessionId = sessionId;
        saveChatSessionId(this.config.cwd, sessionId);
      },
    });

    const readable = Readable.toWeb(
      child.stdout,
    ) as ReadableStream<Uint8Array>;

    const composed = readable
      .pipeThrough(new TextDecoderStream() as unknown as TransformStream<Uint8Array, string>)
      .pipeThrough(createLineSplitter())
      .pipeThrough(transformer);

    child.on('error', (err) => {
      cleanup();
      throw err;
    });

    child.on('exit', (code) => {
      if (code && code !== 0) {
        providerLog(`child exited with code ${code}`);
      }
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
  content: string | Array<{ type: string; text?: string; toolName?: string; input?: unknown; toolCallId?: string }>,
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

function extractToolCallsFromContent(
  content: string | Array<{ type: string; text?: string; toolName?: string; input?: unknown; toolCallId?: string }>,
): NonokaChatToolCall[] | undefined {
  if (typeof content === 'string') {
    return undefined;
  }
  const calls: NonokaChatToolCall[] = [];
  for (const part of content) {
    if (part.type === 'tool-call') {
      calls.push({
        id: part.toolCallId ?? generateId(),
        name: part.toolName ?? '',
        arguments: JSON.stringify(part.input ?? {}),
      });
    }
  }
  return calls.length > 0 ? calls : undefined;
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
