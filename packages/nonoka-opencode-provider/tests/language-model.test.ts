import { describe, expect, it } from 'bun:test';
import fs from 'fs';
import { EventEmitter } from 'events';
import { PassThrough } from 'stream';
import { createNonoka } from '../src/index';
import {
  NonokaLanguageModel,
  PROVIDER_COMPLETE_OBSERVATION_MAX_BYTES,
  PROVIDER_HARD_REQUEST_BYTES,
  PROVIDER_SOFT_REQUEST_BYTES,
  encodeRequestWithRetry,
  getChatSessionIdFile,
  loadChatSessionId,
  saveChatSessionId,
  normalizeExternalToolOutput,
} from '../src/nonoka-language-model';

function makeChatPrompt(newConversation = false): any {
  const messages: any = [
    { role: 'system', content: 'You are helpful.' },
    { role: 'user', content: 'Hello' },
  ];
  if (!newConversation) {
    messages.push({ role: 'assistant', content: 'Hi there.' });
  }
  return messages;
}

function makeTitlePrompt(): any {
  return [
    { role: 'system', content: 'You are helpful.' },
    { role: 'user', content: 'Generate a title for this conversation:\n' },
    { role: 'user', content: 'Hello world' },
  ];
}

describe('NonokaLanguageModel', () => {
  it('forwards provider-level runtime policy into model requests', () => {
    const provider = createNonoka({
      serverCommand: ['nonoka-cli', '--server'],
      cwd: '/tmp/nonoka-provider-policy-test',
      wallTimeoutSeconds: 600,
      maxContextBytes: 262144,
      maxExternalResultBytes: 65536,
      requireWorkspaceMutation: true,
      requireObservedEffect: true,
    });
    const model = provider('deepseek-chat');

    const request = (model as any).buildChatRequest(
      { prompt: makeChatPrompt(true) },
      false,
    );

    expect(request.wall_timeout_seconds).toBe(600);
    expect(request.max_context_bytes).toBe(262144);
    expect(request.max_external_result_bytes).toBe(65536);
    expect(request.require_workspace_mutation).toBe(true);
    expect(request.require_observed_effect).toBe(true);
    expect(request.protocol.version).toBe('1.0');
    expect(request.protocol.required_capabilities).toContain('persistent_runtime_limits');
    expect(request.protocol.provider_version).toBe('0.2.14');
  });

  it('constructs with the given model id and config', () => {
    const model = new NonokaLanguageModel(
      'deepseek-chat',
      { sessionId: 'sess-1' },
      {
        provider: 'nonoka',
        serverCommand: ['nonoka-cli', '--server'],
        cwd: '.',
        configPath: './nonoka.yaml',
        model: 'deepseek-chat',
        env: { KEY: 'value' },
      },
    );

    expect(model.modelId).toBe('deepseek-chat');
    expect(model.provider).toBe('nonoka');
    expect(model.specificationVersion).toBe('v3');
    expect(model.supportedUrls).toEqual({});
  });

  it('throws when serverCommand is empty', () => {
    const model = new NonokaLanguageModel(
      'x',
      {},
      {
        provider: 'nonoka',
        serverCommand: [],
        cwd: '.',
      },
    );

    expect(() => model['spawnServer']()).toThrow('serverCommand must not be empty');
  });

  it('surfaces a child process error through the returned web stream', async () => {
    class FakeChild extends EventEmitter {
      stdin = new PassThrough();
      stdout = new PassThrough();
      stderr = new PassThrough();
      pid = undefined;
      exitCode: number | null = null;
      kill() { return true; }
    }

    const model = new NonokaLanguageModel('deepseek-chat', {}, {
      provider: 'nonoka', serverCommand: ['nonoka-cli', '--server'], cwd: '/tmp/nonoka-child-error',
    });
    const child = new FakeChild();
    const stream = model['createOutputStream'](child as any, false);
    const reader = stream.getReader();

    child.emit('error', new Error('spawn failed'));

    await expect(reader.read()).rejects.toThrow('spawn failed');
  });

  it('detects title generation and merges consecutive user messages', () => {
    const model = new NonokaLanguageModel(
      'deepseek-chat',
      {},
      {
        provider: 'nonoka',
        serverCommand: ['nonoka-cli', '--server'],
        cwd: '/tmp/nonoka-title-test',
      },
    );

    const request = (model as any).buildChatRequest(
      { prompt: makeTitlePrompt() },
      true,
    );

    expect(request.new_session).toBe(true);
    expect(request.purpose).toBe('title');
    expect(request.messages).toHaveLength(2);
    expect(request.messages[0].role).toBe('system');
    expect(request.messages[1].role).toBe('user');
    expect(request.messages[1].content).toContain('Generate a title for this conversation');
    expect(request.messages[1].content).toContain('Hello world');
    expect(request.request_id).toMatch(/^req-/);
  });

  it('uses independent session ids for chat and title', () => {
    const cwd = `/tmp/nonoka-session-isolation-${Date.now()}`;
    const sessionFile = getChatSessionIdFile(cwd);
    try {
      fs.rmSync(sessionFile, { force: true });
      saveChatSessionId(cwd, 'chat-sess-1');

      const model = new NonokaLanguageModel(
        'deepseek-chat',
        {},
        {
          provider: 'nonoka',
          serverCommand: ['nonoka-cli', '--server'],
          cwd,
        },
      );

      // Chat request reuses the persisted chat session id.
      const chatRequest = (model as any).buildChatRequest(
        { prompt: makeChatPrompt(false) },
        false,
      );
      expect(chatRequest.session_id).toBe('chat-sess-1');
      expect(chatRequest.new_session).toBe(false);

      // Title request uses a fresh id and does not touch the chat session file.
      const titleRequest = (model as any).buildChatRequest(
        { prompt: makeTitlePrompt() },
        true,
      );
      expect(titleRequest.session_id).not.toBe('chat-sess-1');
      expect(titleRequest.new_session).toBe(true);
      expect(fs.readFileSync(sessionFile, 'utf-8').trim()).toBe('chat-sess-1');

      // After title, the next chat request still uses the chat session id.
      const chatRequest2 = (model as any).buildChatRequest(
        { prompt: makeChatPrompt(false) },
        false,
      );
      expect(chatRequest2.session_id).toBe('chat-sess-1');
    } finally {
      fs.rmSync(sessionFile, { force: true });
    }
  });

  it('starts a new chat session when isNewConversation is true', () => {
    const cwd = `/tmp/nonoka-new-chat-${Date.now()}`;
    const sessionFile = getChatSessionIdFile(cwd);
    try {
      fs.rmSync(sessionFile, { force: true });

      const model = new NonokaLanguageModel(
        'deepseek-chat',
        {},
        {
          provider: 'nonoka',
          serverCommand: ['nonoka-cli', '--server'],
          cwd,
        },
      );

      const request = (model as any).buildChatRequest(
        { prompt: makeChatPrompt(true) },
        false,
      );
    expect(request.new_session).toBe(true);
    expect(request.purpose).toBe('chat');
      expect(request.session_id).toBeUndefined();
    } finally {
      fs.rmSync(sessionFile, { force: true });
    }
  });

  it('round-trips chat session id through persistence helpers', () => {
    const cwd = `/tmp/nonoka-persist-${Date.now()}`;
    const sessionFile = getChatSessionIdFile(cwd);
    try {
      fs.rmSync(sessionFile, { force: true });
      expect(loadChatSessionId(cwd)).toBeUndefined();
      saveChatSessionId(cwd, 'persisted-sess');
      expect(loadChatSessionId(cwd)).toBe('persisted-sess');
    } finally {
      fs.rmSync(sessionFile, { force: true });
    }
  });

  it('builds chat request with external mcp/skill placeholders', () => {
    const model = new NonokaLanguageModel(
      'deepseek-chat',
      {},
      {
        provider: 'nonoka',
        serverCommand: ['nonoka-cli', '--server'],
        cwd: '/tmp/nonoka-external-test',
      },
    );

    const request = (model as any).buildChatRequest(
      { prompt: makeChatPrompt(true) },
      false,
    );
    expect(request.external_mcp_servers).toEqual([]);
    expect(request.external_skills).toEqual([]);
  });

  it('forwards configured generation limits to the bridge', () => {
    const model = new NonokaLanguageModel(
      'deepseek-chat',
      {},
      {
        provider: 'nonoka',
        serverCommand: ['nonoka-cli', '--server'],
        cwd: '/tmp/nonoka-generation-test',
        temperature: 0,
        maxTurns: 12,
        timeoutSeconds: 90,
        wallTimeoutSeconds: 600,
        toolBudget: 30,
        maxContextBytes: 262144,
        maxExternalResultBytes: 65536,
        requireWorkspaceMutation: true,
        requireObservedEffect: true,
      },
    );

    const request = (model as any).buildChatRequest(
      { prompt: makeChatPrompt(true) },
      false,
    );
    expect(request.temperature).toBe(0);
    expect(request.max_turns).toBe(12);
    expect(request.timeout_seconds).toBe(90);
    expect(request.wall_timeout_seconds).toBe(600);
    expect(request.tool_budget).toBe(30);
    expect(request.max_context_bytes).toBe(262144);
    expect(request.max_external_result_bytes).toBe(65536);
    expect(request.require_workspace_mutation).toBe(true);
    expect(request.require_observed_effect).toBe(true);
  });

  it('keeps only the latest contiguous external tool-result batch on resume', () => {
    const model = new NonokaLanguageModel('deepseek-chat', {}, {
      provider: 'nonoka', serverCommand: ['nonoka-cli', '--server'], cwd: '/tmp/nonoka-resume-test',
    });
    const request = (model as any).buildChatRequest({ prompt: [
      { role: 'system', content: 'system' },
      { role: 'user', content: 'first' },
      { role: 'assistant', content: [{ type: 'tool-call', toolCallId: 'old', toolName: 'bash', input: {} }] },
      { role: 'tool', content: [{ type: 'tool-result', toolCallId: 'old', toolName: 'bash', output: { type: 'text', value: 'old result' } }] },
      { role: 'assistant', content: [{ type: 'tool-call', toolCallId: 'new-1', toolName: 'bash', input: {} }, { type: 'tool-call', toolCallId: 'new-2', toolName: 'read', input: {} }] },
      { role: 'tool', content: [
        { type: 'tool-result', toolCallId: 'new-1', toolName: 'bash', output: { type: 'text', value: 'new result 1' } },
        { type: 'tool-result', toolCallId: 'new-2', toolName: 'read', output: { type: 'text', value: 'new result 2' } },
      ] },
    ] }, false);
    expect(request.messages.map((message: any) => message.tool_call_id).filter(Boolean)).toEqual(['new-1', 'new-2']);
    expect(JSON.stringify(request.messages)).not.toContain('old result');
  });

  it('performs one size compaction retry without changing the session id', () => {
    const request: any = {
      type: 'chat', session_id: 'sess-keep', cwd: '/tmp',
      messages: [{ role: 'tool', tool_call_id: 'tc', content: 'x'.repeat(PROVIDER_SOFT_REQUEST_BYTES * 2), result: 'x'.repeat(PROVIDER_SOFT_REQUEST_BYTES * 2) }],
    };
    const line = encodeRequestWithRetry(request);
    const parsed = JSON.parse(line);
    expect(parsed.session_id).toBe('sess-keep');
    expect(Buffer.byteLength(line)).toBeLessThan(PROVIDER_HARD_REQUEST_BYTES);
    expect(parsed.messages[0].content).toContain('compacted by nonoka provider');
    expect(parsed.messages[0].result.completeness).toBe('partial');
  });

  it('attests small tool output as complete', () => {
    const receipt = normalizeExternalToolOutput('/tmp', 'small-call', 'custom', 'bounded');

    expect(receipt.completeness).toBe('complete');
    expect(receipt.truncated).toBe(false);
    expect(receipt.artifact_ref).toBeUndefined();
  });

  it('conservatively attests soft-threshold output as partial without tool-name rules', () => {
    const receipt = normalizeExternalToolOutput(
      '/tmp',
      'soft-call',
      'arbitrary_tool',
      'x'.repeat(PROVIDER_COMPLETE_OBSERVATION_MAX_BYTES),
    );

    expect(receipt.completeness).toBe('partial');
    expect(receipt.truncated).toBe(false);
    expect(receipt.artifact_ref).toBeString();
  });

  it('attests provider-truncated tool output as partial', () => {
    const receipt = normalizeExternalToolOutput(
      '/tmp',
      'hard-call',
      'write',
      'x'.repeat(PROVIDER_COMPLETE_OBSERVATION_MAX_BYTES * 2),
    );

    expect(receipt.completeness).toBe('partial');
    expect(receipt.truncated).toBe(true);
    expect(receipt.artifact_ref).toBeString();
  });

  it('attests host-level record truncation as partial', () => {
    const receipt = normalizeExternalToolOutput(
      '/tmp',
      'host-truncated-call',
      'grep',
      'Found 1 match\n/app/data.json:\n  Line 18: prefix... (line truncated to 2000 chars)',
      { pattern: 'hf_[a-zA-Z0-9]{34}', path: '/app' },
    );

    expect(receipt.completeness).toBe('partial');
    expect(receipt.truncated).toBe(false);
    expect(receipt.artifact_ref).toBeString();
  });

  it('renders bounded candidate evidence from a large structured pattern search', () => {
    const receipt = normalizeExternalToolOutput(
      '/tmp',
      'pattern-call',
      'grep',
      `${'prefix '.repeat(1800)}\n/app/data.json:\n  Line 18: payload hf_abcdefgh end\n`,
      { pattern: 'hf_[a-z]{8}', path: '/app' },
    );

    expect(receipt.completeness).toBe('partial');
    expect(receipt.artifact_ref).toBeString();
    expect(String(receipt.result)).toContain('[Pattern-match evidence]');
    expect(String(receipt.result)).toContain('hf_abcdefgh');
    expect(String(receipt.result)).toContain('unresolved candidate');
  });

  it('retains opaque candidates when a broad alternative has many ordinary matches', () => {
    const receipt = normalizeExternalToolOutput(
      '/tmp',
      'broad-pattern-call',
      'grep',
      `${'token documentation\n'.repeat(1000)}embedded value hf_abcdefghijklmnopqrstuvwxyz123456`,
      { pattern: 'token|hf_' },
    );

    expect(String(receipt.result)).toContain('hf_abcdefghijklmnopqrstuvwxyz123456');
  });

  it('marks a host tool failure as partial evidence with a bounded fallback instruction', () => {
    const receipt = normalizeExternalToolOutput(
      '/tmp',
      'failed-search',
      'grep',
      'record exceeds host output limit',
      { pattern: 'example' },
      true,
    );

    expect(receipt.completeness).toBe('partial');
    expect(receipt.artifact_ref).toBeString();
    expect(String(receipt.result)).toContain('[Host tool failure]');
    expect(String(receipt.result)).toContain('bounded fallback');
  });

  it('forwards a tool call pattern when rendering its subsequent result', () => {
    const model = new NonokaLanguageModel('deepseek-chat', {}, {
      provider: 'nonoka', serverCommand: ['nonoka-cli', '--server'], cwd: '/tmp/nonoka-pattern-forwarding',
    });
    const request = (model as any).buildChatRequest({ prompt: [
      { role: 'system', content: 'system' },
      { role: 'user', content: 'find candidates' },
      { role: 'assistant', content: [{
        type: 'tool-call', toolCallId: 'pattern-call', toolName: 'grep',
        input: { pattern: 'key_[a-z]{8}', path: '/app' },
      }] },
      { role: 'tool', content: [{
        type: 'tool-result', toolCallId: 'pattern-call', toolName: 'grep',
        output: { type: 'text', value: `${'x '.repeat(5000)} key_abcdefgh` },
      }] },
    ] }, false);

    expect(request.messages.at(-1).content).toContain('[Pattern-match evidence]');
    expect(request.messages.at(-1).content).toContain('key_abcdefgh');
  });

  it('strips tools and external definitions during title generation', () => {
    const model = new NonokaLanguageModel(
      'deepseek-chat',
      {},
      {
        provider: 'nonoka',
        serverCommand: ['nonoka-cli', '--server'],
        cwd: '/tmp/nonoka-title-tools-test',
      },
    );

    const request = (model as any).buildChatRequest(
      {
        prompt: makeTitlePrompt(),
        tools: [
          {
            type: 'function',
            name: 'load_skill',
            description: 'Load a skill',
            inputSchema: { type: 'object', properties: {} },
          },
        ],
      },
      true,
    );
    expect(request.tools).toBeUndefined();
    expect(request.external_mcp_servers).toEqual([]);
    expect(request.external_skills).toEqual([]);
  });

  it('uses a minimal system prompt for title generation', () => {
    const model = new NonokaLanguageModel(
      'deepseek-chat',
      {},
      {
        provider: 'nonoka',
        serverCommand: ['nonoka-cli', '--server'],
        cwd: '/tmp/nonoka-title-prompt-test',
      },
    );

    const request = (model as any).buildChatRequest(
      {
        prompt: [
          { role: 'system', content: 'You are nonoka-cli with load_skill tools.' },
          { role: 'user', content: 'Generate a title for this conversation:\n' },
          { role: 'user', content: 'hello world' },
        ],
      },
      true,
    );
    expect(request.messages).toHaveLength(2);
    expect(request.messages[0].role).toBe('system');
    expect(request.messages[0].content).toContain('Generate a concise');
    expect(request.messages[1].role).toBe('user');
    expect(request.messages[1].content).toContain('Generate a title');
    expect(request.messages[1].content).toContain('hello world');
  });
});
