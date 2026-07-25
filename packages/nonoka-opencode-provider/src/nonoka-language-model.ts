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
  encodeCancelMessage,
  NONOKA_MESSAGE_ROLES,
  encodeChatRequest,
  type NonokaChatMessage,
  type NonokaChatRequest,
  type NonokaChatToolCall,
  type NonokaExternalToolReceipt,
} from './protocol.js';
import { createNonokaStreamTransformer } from './stream.js';
import fs from 'fs';
import { receiptForWorkspaceResult } from './workspace.js';

const PROVIDER_LOG_PATH = process.env.NONOKA_PROVIDER_LOG_PATH;

function providerLog(message: string) {
  if (!PROVIDER_LOG_PATH) return;
  try {
    fs.appendFileSync(PROVIDER_LOG_PATH, `${new Date().toISOString()} ${message}\n`);
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
export const PROVIDER_SOFT_REQUEST_BYTES = 256 * 1024;
export const PROVIDER_HARD_REQUEST_BYTES = 1024 * 1024;
export const PROVIDER_COMPLETE_OBSERVATION_MAX_BYTES = 8 * 1024;

function utf8Bytes(value: string): number {
  return Buffer.byteLength(value, 'utf8');
}

function truncateUtf8(value: string, maxBytes: number): string {
  if (utf8Bytes(value) <= maxBytes) return value;
  const marker = '\n...[compacted by nonoka provider]...\n';
  const markerBytes = utf8Bytes(marker);
  if (maxBytes <= markerBytes + 2) {
    return Buffer.from(value).subarray(0, Math.max(0, maxBytes)).toString('utf8');
  }
  const encoded = Buffer.from(value, 'utf8');
  const side = Math.max(1, Math.floor((maxBytes - markerBytes) / 2));
  return Buffer.concat([
    encoded.subarray(0, side), Buffer.from(marker), encoded.subarray(encoded.length - side),
  ]).toString('utf8');
}

function outputKind(toolName: string): string {
  const name = toolName.toLowerCase();
  if (['bash', 'execute_command', 'terminal'].includes(name)) return 'shell';
  if (['read', 'read_file', 'view'].includes(name)) return 'read';
  if (['write', 'write_file', 'edit', 'edit_file', 'apply_patch', 'delete_file'].includes(name)) {
    return 'mutation';
  }
  if (name === 'task') return 'delegated';
  return 'tool';
}

function outputLimit(toolName: string): number {
  switch (outputKind(toolName)) {
    case 'mutation': return 8 * 1024;
    case 'read': return 64 * 1024;
    case 'shell': return 48 * 1024;
    case 'delegated': return 24 * 1024;
    default: return 32 * 1024;
  }
}

/** Host tools may truncate individual records while returning a small overall
 * payload. Those observations are still partial even when the provider does
 * not need to truncate the serialized response itself. */
function hasHostTruncationMarker(output: string): boolean {
  return /\bmore matches available\b/i.test(output)
    || /\bline truncated to \d+ chars\b/i.test(output)
    || /\boutput truncated\b/i.test(output)
    || /\bfull output (?:is )?available at\b/i.test(output);
}

/**
 * Extract stable literal fragments from a structured search pattern without
 * evaluating model-supplied regular expressions in the provider.  Running an
 * arbitrary expression over a large host result here would create a second
 * ReDoS surface.  The fragments are only used to make an already-returned
 * partial observation legible; they do not decide whether a match is valid.
 */
function patternAnchors(pattern: string): string[] {
  const withoutClasses = pattern
    .replace(/\[[^\]]*\]/g, ' ')
    .replace(/\\[dDsSwWbBtrnvf]/g, ' ')
    .replace(/\\[^A-Za-z0-9_-]/g, ' ');
  const candidates = withoutClasses.match(/[A-Za-z0-9_-]{3,}/g) ?? [];
  return [...new Set(candidates)]
    .sort((left, right) => right.length - left.length)
    .slice(0, 3);
}

function compactExcerpt(value: string): string {
  return value.replace(/\s+/g, ' ').trim();
}

/**
 * Render candidate-centered evidence for a large structured search result.
 *
 * OpenCode's search tools can report a single JSON record hundreds of KiB
 * long.  A generic head/tail truncation preserves bytes but can bury the
 * actual match among unrelated fields.  If a tool call supplied a ``pattern``
 * argument, retain bounded windows around literal anchors from that pattern.
 * The receipt remains partial: this renderer makes candidates actionable, it
 * never claims the search was exhaustive or that a candidate is benign.
 */
export function renderPatternEvidence(
  toolArguments: Record<string, unknown> | undefined,
  output: string,
): string | undefined {
  const pattern = toolArguments?.pattern;
  if (typeof pattern !== 'string' || pattern.length === 0 || pattern.length > 4096) {
    return undefined;
  }
  const anchors = patternAnchors(pattern);
  if (anchors.length === 0) return undefined;

  const candidateOffsets: number[] = [];
  const seenOffsets = new Set<number>();
  const addOffset = (offset: number) => {
    if (seenOffsets.has(offset)) return;
    seenOffsets.add(offset);
    candidateOffsets.push(offset);
  };
  for (const anchor of anchors) {
    let start = 0;
    let count = 0;
    // Give every alternative in a structured search a bounded share.  A
    // common word in one alternative must not hide a rarer candidate from a
    // later alternative.
    while (count < 2) {
      const offset = output.indexOf(anchor, start);
      if (offset < 0) break;
      start = offset + Math.max(1, anchor.length);
      addOffset(offset);
      count += 1;
    }
  }

  // Opaque, long identifiers are high-signal candidates even when the search
  // pattern also contains broad words such as "token".  This static matcher
  // is deliberately independent of credential vendors and avoids evaluating
  // the model-supplied expression a second time.
  const opaque = /\b[A-Za-z][A-Za-z0-9-]{1,15}_[A-Za-z0-9_-]{16,}\b/g;
  for (let match = opaque.exec(output); match && candidateOffsets.length < 12; match = opaque.exec(output)) {
    addOffset(match.index);
  }
  if (candidateOffsets.length === 0) return undefined;

  candidateOffsets.sort((left, right) => left - right);
  const rendered = candidateOffsets.map((offset, index) => {
    const before = Math.max(0, offset - 100);
    const after = Math.min(output.length, offset + 220);
    return `${index + 1}. ${compactExcerpt(output.slice(before, after))}`;
  }).join('\n');
  return [
    '[Pattern-match evidence]',
    `The host result contains ${candidateOffsets.length} candidate occurrence(s) related to the structured search pattern ${JSON.stringify(pattern)}.`,
    'This observation is partial. Treat every occurrence below as an unresolved candidate: inspect its smallest source record or region before declaring it benign or completing the task.',
    rendered,
  ].join('\n');
}

function renderObservation(
  output: string,
  limit: number,
  evidence: string | undefined,
): { result: string; truncated: boolean } {
  const raw = utf8Bytes(output) > limit ? truncateUtf8(output, limit) : output;
  if (!evidence) return { result: raw, truncated: raw !== output };

  const separator = '\n\n[Raw host-result excerpt]\n';
  const evidenceBytes = utf8Bytes(evidence);
  // The evidence itself is bounded by a fixed candidate count.  Retain at
  // least a small raw excerpt as a fallback for hosts with unusual output.
  const rawBudget = Math.max(512, limit - evidenceBytes - utf8Bytes(separator));
  const rawExcerpt = truncateUtf8(output, rawBudget);
  return {
    result: `${evidence}${separator}${rawExcerpt}`,
    truncated: rawExcerpt !== output,
  };
}

function spillToolOutput(cwd: string, toolCallId: string, toolName: string, output: string): string | undefined {
  try {
    const root = process.env.NONOKA_TRACE_DIR || path.join('/tmp', `nonoka-trace-${cwdHash(cwd)}`);
    const directory = path.join(root, 'tool-output');
    fs.mkdirSync(directory, { recursive: true });
    const safeName = (toolName || 'tool').replace(/[^a-zA-Z0-9_-]/g, '_');
    const safeId = (toolCallId || generateId()).replace(/[^a-zA-Z0-9_-]/g, '_');
    const artifact = path.join(directory, `${safeName}-${safeId}.txt`);
    fs.writeFileSync(artifact, output, 'utf8');
    return artifact;
  } catch (err) {
    providerLog(`failed to spill tool output: ${err}`);
    return undefined;
  }
}

export function normalizeExternalToolOutput(
  cwd: string,
  toolCallId: string,
  toolName: string,
  output: string,
  toolArguments?: Record<string, unknown>,
  hostObservationFailure = false,
  exitCode?: number,
): NonokaExternalToolReceipt {
  const originalBytes = utf8Bytes(output);
  const limit = outputLimit(toolName);
  const partial = hostObservationFailure
    || hasHostTruncationMarker(output)
    || originalBytes >= PROVIDER_COMPLETE_OBSERVATION_MAX_BYTES;
  const evidence = partial ? renderPatternEvidence(toolArguments, output) : undefined;
  const rendered = renderObservation(output, limit, evidence);
  const truncated = rendered.truncated;
  const completeness = (
    truncated || partial
      ? 'partial'
      : 'complete'
  );
  const artifactRef = completeness === 'partial'
    ? spillToolOutput(cwd, toolCallId, toolName, output)
    : undefined;
  const failureNotice = hostObservationFailure
    ? '[Host tool failure] This result could not be fully observed and cannot establish absence or coverage. Use a bounded fallback that reports match snippets or source coordinates; do not repeat the same failed query.\n\n'
    : '';
  return {
    result: `${failureNotice}${rendered.result}`,
    exit_code: exitCode ?? (hostObservationFailure ? 1 : undefined),
    host: 'opencode',
    artifact_ref: artifactRef,
    output_kind: outputKind(toolName),
    original_bytes: originalBytes,
    truncated,
    completeness,
  };
}

function compactReceipt(value: unknown, maxBytes: number, fallback: string): unknown {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    const payload = typeof value === 'string' ? value : fallback;
    if (utf8Bytes(payload) <= maxBytes) return value;
    return {
      result: truncateUtf8(payload, maxBytes),
      host: 'opencode',
      original_bytes: utf8Bytes(payload),
      truncated: true,
      completeness: 'partial',
    };
  }
  const receipt = { ...(value as Record<string, unknown>) };
  const payload = typeof receipt.result === 'string' ? receipt.result : fallback;
  if (utf8Bytes(payload) > maxBytes) {
    receipt.result = truncateUtf8(payload, maxBytes);
    receipt.truncated = true;
    receipt.completeness = 'partial';
    if (receipt.original_bytes === undefined) receipt.original_bytes = utf8Bytes(payload);
  }
  return receipt;
}

export function compactToolBatch(
  messages: NonokaChatMessage[],
  byteBudget: number,
): NonokaChatMessage[] {
  if (messages.length === 0) return [];
  const perPayload = Math.max(1024, Math.floor(byteBudget / (messages.length * 3)));
  return messages.map((message) => {
    if (message.role !== NONOKA_MESSAGE_ROLES.tool) return message;
    const content = truncateUtf8(message.content, perPayload);
    return {
      ...message,
      content,
      result: compactReceipt(message.result, perPayload, content),
    };
  });
}

export function encodeRequestWithRetry(request: NonokaChatRequest): string {
  let line = encodeChatRequest(request) + '\n';
  if (utf8Bytes(line) <= PROVIDER_SOFT_REQUEST_BYTES) return line;

  // One deterministic compaction retry.  The session id and pending result
  // receipts are retained; this never starts a fresh Nonoka session.
  const compacted: NonokaChatRequest = {
    ...request,
    messages: compactToolBatch(request.messages, PROVIDER_SOFT_REQUEST_BYTES / 2),
  };
  line = encodeChatRequest(compacted) + '\n';
  providerLog(`request compaction retry bytes=${utf8Bytes(line)}`);
  if (utf8Bytes(line) > PROVIDER_SOFT_REQUEST_BYTES) {
    throw new Error(JSON.stringify({
      code: 'request_budget_exhausted',
      message: 'Nonoka bridge request exceeds the 256 KiB provider budget after compaction.',
      retryable: false,
      bytes: utf8Bytes(line),
      max_bytes: PROVIDER_SOFT_REQUEST_BYTES,
      hard_frame_bytes: PROVIDER_HARD_REQUEST_BYTES,
    }));
  }
  return line;
}

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
  temperature?: number;
  maxTurns?: number;
  timeoutSeconds?: number;
  wallTimeoutSeconds?: number;
  toolBudget?: number;
  maxContextBytes?: number;
  maxExternalResultBytes?: number;
  requireWorkspaceMutation?: boolean;
  requireObservedEffect?: boolean;
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
  private cachedChatTools:
    | { name: string; description: string; parameters: Record<string, unknown> }[]
    | undefined;

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
    const request = this.buildChatRequest(options, isTitle);
    const requestLine = encodeRequestWithRetry(request);
    const child = this.spawnServer();
    providerLog(`spawned child pid=${child.pid}`);
    providerLog(`sending request bytes=${Buffer.byteLength(requestLine)}`);
    await writeToStdin(child, requestLine);

    const allowedToolNames = new Set(
      (options.tools ?? [])
        .filter((t: any): t is { type: 'function'; name: string } => t.type === 'function')
        .map((t) => t.name),
    );
    providerLog(`allowedToolNames count=${allowedToolNames.size} names=${JSON.stringify([...allowedToolNames])}`);

    const rawStream = this.createOutputStream(child, isTitle, options.abortSignal, allowedToolNames);

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
    const isResume = this.isExternalToolResume(options.prompt);
    const messages = this.convertPromptMessages(options.prompt, isTitle, isResume);

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

    let tools = options.tools
      ?.filter((tool): tool is { type: 'function'; name: string; description?: string; inputSchema: Record<string, unknown> } => tool.type === 'function')
      .map((tool) => ({
        name: tool.name,
        description: tool.description ?? '',
        parameters: tool.inputSchema,
      }));

    // OpenCode sometimes sends resume requests without repeating the tool list.
    // Cache the tools from the first real chat request so nonoka-cli can stay
    // in external-tool mode across turns.
    if (!isTitle && tools && tools.length > 0) {
      this.cachedChatTools = tools;
    }
    if (!isTitle && isResume && (!tools || tools.length === 0) && this.cachedChatTools) {
      tools = this.cachedChatTools;
      providerLog(`buildChatRequest resumed with cached tools count=${tools.length}`);
    }

    // OpenCode 1.17.14 does not expose external MCP/skill definitions to
    // providers. The bridge fields are reserved so the protocol is ready when
    // a future OpenCode version (or a custom host) passes them.
    let externalMcpServers: any[] = [];
    let externalSkills: any[] = [];

    // Title generation should receive the minimal prompt only. Exposing the
    // full tool list (including load_skill) lets the model make tool calls in
    // a context where OpenCode expects a plain title string, which breaks the
    // subsequent resume turn.
    if (isTitle) {
      tools = undefined;
      externalMcpServers = [];
      externalSkills = [];
    }

    const systemMessages = messages.filter((message) => message.role === NONOKA_MESSAGE_ROLES.system);
    const hasProgressGuidance = systemMessages.some((message) => (
      /\[(?:Execution phase|Completion evidence|Verification budget|Workspace progress)\]/.test(message.content)
    ));
    providerLog(
      `buildChatRequest isTitle=${isTitle} isResume=${isResume} `
      + `systemMessages=${systemMessages.length} hasProgressGuidance=${hasProgressGuidance} `
      + `tools count=${tools?.length ?? 0} names=${JSON.stringify(tools?.map((t) => t.name))}`,
    );
    return {
      type: NONOKA_INBOUND_TYPES.chat,
      purpose: isTitle ? 'title' : 'chat',
      messages,
      tools,
      external_mcp_servers: externalMcpServers,
      external_skills: externalSkills,
      session_id: sessionId,
      new_session: newSession,
      cwd: this.config.cwd,
      model: this.config.model,
      temperature: this.config.temperature,
      max_turns: this.config.maxTurns,
      timeout_seconds: this.config.timeoutSeconds,
      wall_timeout_seconds: this.config.wallTimeoutSeconds,
      tool_budget: this.config.toolBudget,
      max_context_bytes: this.config.maxContextBytes,
      max_external_result_bytes: this.config.maxExternalResultBytes,
      require_workspace_mutation: this.config.requireWorkspaceMutation,
      require_observed_effect: this.config.requireObservedEffect,
      request_id: generateRequestId(),
    };
  }

  private convertPromptMessages(
    prompt: LanguageModelV3CallOptions['prompt'],
    isTitle: boolean,
    isResume: boolean,
  ): NonokaChatMessage[] {
    const messages: NonokaChatMessage[] = [];
    const toolNamesByCallId = new Map<string, string>();
    const toolArgumentsByCallId = new Map<string, Record<string, unknown>>();

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
          for (const toolCall of toolCalls ?? []) {
            toolNamesByCallId.set(toolCall.id, toolCall.name);
            const argumentsValue = parseToolArguments(toolCall.arguments);
            if (argumentsValue) toolArgumentsByCallId.set(toolCall.id, argumentsValue);
          }
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
              const toolName = (part as any).toolName ?? toolNamesByCallId.get(part.toolCallId) ?? '';
              const exitCode = extractHostExitCode(part);
              providerLog(
                `tool result tool=${toolName} exitCode=${exitCode ?? 'unknown'} `
                + `partKeys=${Object.keys(part as any).sort().join(',')} `
                + `providerMetadataKeys=${Object.keys((part as any).providerMetadata ?? {}).sort().join(',')}`,
              );
              const receipt = normalizeExternalToolOutput(
                this.config.cwd,
                part.toolCallId,
                toolName,
                outputText,
                toolArgumentsByCallId.get(part.toolCallId),
                isIncompleteHostToolOutput(part.output),
                exitCode,
              );
              messages.push({
                role: NONOKA_MESSAGE_ROLES.tool,
                content: String(receipt.result ?? ''),
                tool_call_id: part.toolCallId,
                result: receiptForWorkspaceResult(
                  this.config.cwd,
                  part.toolCallId,
                  toolName,
                  receipt,
                  toolArgumentsByCallId.get(part.toolCallId),
                ),
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

    if (isResume) {
      // When resuming after external tool execution, nonoka's checkpoint already
      // stores the full conversation. Sending the entire history again would
      // duplicate messages and can reorder context. Keep only the system prompt
      // (so the host's agent prompt is still forwarded) and the tool results
      // that are needed to continue.
      const systemMessages = messages.filter((m) => m.role === NONOKA_MESSAGE_ROLES.system);
      const toolMessages: NonokaChatMessage[] = [];
      for (let index = messages.length - 1; index >= 0; index -= 1) {
        const message = messages[index];
        if (!message || message.role !== NONOKA_MESSAGE_ROLES.tool) break;
        toolMessages.unshift(message);
      }
      const boundedToolMessages = compactToolBatch(toolMessages, PROVIDER_SOFT_REQUEST_BYTES);
      providerLog(`resume message compaction: kept ${systemMessages.length} system + ${toolMessages.length} tool messages (dropped ${messages.length - systemMessages.length - toolMessages.length})`);
      return [...systemMessages, ...boundedToolMessages];
    }

    if (isTitle) {
      // Title generation must stay minimal. The host's full agent prompt may
      // expose tools and skill instructions that are not valid in a title-only
      // turn, causing the model to emit tool_calls and break the API contract.
      const userMessages = messages.filter((m) => m.role === NONOKA_MESSAGE_ROLES.user);
      const titleSystem: NonokaChatMessage = {
        role: NONOKA_MESSAGE_ROLES.system,
        content: 'Generate a concise, plain-text title for the conversation. Do not call any tools.',
      };
      providerLog(`title message compaction: kept 1 system + ${userMessages.length} user messages (dropped ${messages.length - userMessages.length - 1})`);
      return [titleSystem, ...userMessages];
    }

    return messages;
  }

  private isNewConversation(options: LanguageModelV3CallOptions): boolean {
    // OpenCode's /new resets the message history to system + user only.
    // If we see no prior assistant or tool messages, treat this as a fresh
    // nonoka session. This also covers the very first request of a brand-new
    // OpenCode conversation.
    for (const message of options.prompt) {
      if (message.role === 'assistant' || message.role === 'tool') {
        return false;
      }
    }
    return true;
  }

  private isExternalToolResume(
    prompt: LanguageModelV3CallOptions['prompt'],
  ): boolean {
    // A resume after external tool execution is signaled by the most recent
    // message being a tool result. In that case we should not re-send the full
    // conversation history; the nonoka checkpoint already stores it.
    const last = prompt[prompt.length - 1];
    return last !== undefined && last.role === 'tool';
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
      detached: process.platform !== 'win32',
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
    const pid = child.pid;
    try {
      if (pid && process.platform !== 'win32') process.kill(-pid, 'SIGTERM');
      else child.kill('SIGTERM');
    } catch {}
    const timer = setTimeout(() => {
      if (child.exitCode !== null) return;
      try {
        if (pid && process.platform !== 'win32') process.kill(-pid, 'SIGKILL');
        else child.kill('SIGKILL');
      } catch {}
    }, 5000);
    timer.unref();
  }

  private createOutputStream(
    child: ChildProcessWithoutNullStreams,
    isTitle: boolean,
    abortSignal?: AbortSignal,
    allowedToolNames?: Set<string>,
  ): ReadableStream<LanguageModelV3StreamPart> {
    let cleanupDone = false;

    const cleanup = () => {
      if (cleanupDone) return;
      cleanupDone = true;
      try {
        child.stdin.destroy();
      } catch {}
      this.killChild(child);
    };

    if (abortSignal) {
      abortSignal.addEventListener('abort', () => {
        void writeToStdin(child, encodeCancelMessage({ type: NONOKA_INBOUND_TYPES.cancel }) + '\n')
          .finally(cleanup);
      }, { once: true });
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
      allowedToolNames,
      cwd: this.config.cwd,
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

function parseToolArguments(argumentsValue: string): Record<string, unknown> | undefined {
  try {
    const parsed = JSON.parse(argumentsValue);
    return typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed)
      ? parsed as Record<string, unknown>
      : undefined;
  } catch {
    return undefined;
  }
}

function isIncompleteHostToolOutput(output: { type: string }): boolean {
  return output.type === 'execution-denied'
    || output.type === 'error-text'
    || output.type === 'error-json';
}

function extractHostExitCode(part: any): number | undefined {
  const candidates = [
    part?.exitCode,
    part?.exit_code,
    part?.metadata?.exit,
    part?.metadata?.exitCode,
    part?.providerMetadata?.exit,
    part?.providerMetadata?.exitCode,
    part?.providerMetadata?.opencode?.exit,
    part?.providerMetadata?.opencode?.exitCode,
    part?.output?.exit,
    part?.output?.exitCode,
    part?.output?.exit_code,
    part?.output?.value?.exit,
    part?.output?.value?.exitCode,
    part?.output?.value?.metadata?.exit,
    part?.output?.value?.metadata?.exitCode,
  ];
  for (const value of candidates) {
    if (typeof value === 'number' && Number.isFinite(value)) return Math.trunc(value);
    if (typeof value === 'string' && /^-?\d+$/.test(value)) return Number(value);
  }
  return undefined;
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
