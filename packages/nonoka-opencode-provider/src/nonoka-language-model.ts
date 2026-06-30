import type {
  LanguageModelV3,
  LanguageModelV3CallOptions,
  LanguageModelV3GenerateResult,
  LanguageModelV3StreamPart,
  SharedV3Warning,
} from '@ai-sdk/provider';
import { spawn, type ChildProcessWithoutNullStreams } from 'child_process';
import { Readable } from 'stream';
import type {
  NonokaChatMessage,
  NonokaChatRequest,
} from './protocol.js';
import { encodeChatRequest } from './protocol.js';
import { createNonokaStreamTransformer } from './stream.js';

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
    const request = this.buildChatRequest(options);

    const child = this.spawnServer();

    const requestLine = encodeChatRequest(request) + '\n';
    child.stdin.write(requestLine, (err) => {
      if (err) {
        // The child may already be closed; ignore write errors here.
      }
    });

    const stream = this.createOutputStream(child, options.abortSignal);

    return { stream, warnings };
  }

  private buildChatRequest(options: LanguageModelV3CallOptions): NonokaChatRequest {
    const messages: NonokaChatMessage[] = [];

    for (const message of options.prompt) {
      switch (message.role) {
        case 'system': {
          messages.push({ role: 'system', content: message.content as string });
          break;
        }
        case 'user': {
          const text = extractTextFromContent(message.content);
          messages.push({ role: 'user', content: text });
          break;
        }
        case 'assistant': {
          const text = extractTextFromContent(message.content);
          messages.push({ role: 'assistant', content: text });
          break;
        }
        case 'tool': {
          for (const part of message.content) {
            if (part.type === 'tool-result') {
              const outputText = extractToolOutputText(part.output);
              messages.push({
                role: 'tool',
                content: outputText,
                tool_call_id: part.toolCallId,
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

    return {
      type: 'chat',
      messages,
      session_id: this.sessionId,
      cwd: this.config.cwd,
      model: this.config.model,
    };
  }

  private spawnServer(): ChildProcessWithoutNullStreams {
    const [command, ...args] = this.config.serverCommand;
    if (!command) {
      throw new Error('serverCommand must not be empty');
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
