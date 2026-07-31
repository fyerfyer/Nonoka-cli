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
  attachVerificationReceipt,
  extractHostShellReceipt,
  prepareHostShellCommand,
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
      hostShellEnv: { TASK_ENV: 'testbed' },
      hostShellInit: ['source /opt/task-env.sh'],
    });
    const model = provider('deepseek-chat');

    expect((model as any).config.hostShellEnv).toEqual({ TASK_ENV: 'testbed' });
    expect((model as any).config.hostShellInit).toEqual(['source /opt/task-env.sh']);

    const request = (model as any).buildChatRequest(
      { prompt: makeChatPrompt(true) },
      false,
    );

    expect(request.wall_timeout_seconds).toBe(600);
    expect(request.max_context_bytes).toBe(262144);
    expect(request.max_external_result_bytes).toBe(65536);
    expect(request.require_workspace_mutation).toBe(true);
    expect(request.require_observed_effect).toBe(true);
    expect(request.protocol.version).toBe('1.1');
    expect(request.protocol.required_capabilities).toContain('persistent_runtime_limits');
    expect(request.protocol.required_capabilities).toContain('typed_verification_receipts');
    expect(request.protocol.provider_version).toBe('0.2.16');
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

    await expect(reader.read()).rejects.toThrow('Nonoka provider failed to initialize');
    const child2 = new FakeChild();
    const reader2 = model['createOutputStream'](child2 as any, false).getReader();
    child2.emit('error', new Error('spawn failed'));
    await expect(reader2.read()).rejects.toThrow('nonoka-opencode-provider@0.2.16');
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
        maxCompletionCorrections: 3,
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
    expect(request.max_completion_corrections).toBe(3);
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

  it('wraps shell commands with pipefail and extracts the real exit code', () => {
    const prepared = prepareHostShellCommand(
      'call-verify', 'bash', { command: 'pytest -q | tail -20', timeout: 30 },
    );

    expect(prepared.command).toBe('pytest -q | tail -20');
    expect(prepared.args.command).toContain('bash -o pipefail -c');
    expect(prepared.timeoutSeconds).toBe(30);
    const extracted = extractHostShellReceipt(
      `one failed\n${prepared.marker}=1\n`, prepared.marker,
    );
    expect(extracted.exitCode).toBe(1);
    expect(extracted.output).toBe('one failed');
  });

  it('applies explicit host shell environment and initialization before commands', () => {
    const prepared = prepareHostShellCommand(
      'call-env',
      'bash',
      { command: 'python -m pytest -q' },
      {
        env: { PATH: '/opt/miniconda3/envs/testbed/bin:/usr/bin', TASK_MODE: 'swe bench' },
        init: [
          'source /opt/miniconda3/etc/profile.d/conda.sh',
          'conda activate testbed',
        ],
      },
    );

    const wrapped = String(prepared.args.command);
    expect(wrapped).toContain('export PATH=');
    expect(wrapped).toContain('/opt/miniconda3/envs/testbed/bin:/usr/bin');
    expect(wrapped).toContain('export TASK_MODE=');
    expect(wrapped).toContain('swe bench');
    expect(wrapped).toContain('source /opt/miniconda3/etc/profile.d/conda.sh');
    expect(wrapped).toContain('conda activate testbed');
    expect(wrapped.indexOf('conda activate testbed')).toBeLessThan(
      wrapped.indexOf('python -m pytest -q'),
    );
    expect(wrapped).toContain('if [ "$__nonoka_init_rc" -ne 0 ]');
  });

  it('rejects invalid host shell environment names', () => {
    expect(() => prepareHostShellCommand(
      'call-invalid-env',
      'bash',
      { command: 'true' },
      { env: { 'BAD-NAME': 'value' } },
    )).toThrow('Invalid host shell environment variable name');
  });

  it('creates a passed focused pytest receipt only with collected tests', () => {
    const receipt = normalizeExternalToolOutput(
      '/tmp', 'verify-pass', 'bash', '2 passed in 0.10s', undefined, false, 0,
    );
    attachVerificationReceipt(
      receipt, 'NONOKA_VERIFY=focused pytest -q tests/test_api.py', '/tmp', 30, false,
    );

    expect(receipt.verification?.status).toBe('passed');
    expect(receipt.verification?.collected_tests).toBe(2);
    expect(receipt.verification?.executed_tests).toBe(2);
    expect(receipt.verification?.level).toBe('focused');
  });

  it('accepts a repository bin/test runner as focused test verification', () => {
    const receipt = normalizeExternalToolOutput(
      '/tmp', 'verify-project-runner', 'bash',
      'tests finished: 30 passed, in 0.08 seconds', undefined, false, 0,
    );
    attachVerificationReceipt(
      receipt,
      'NONOKA_VERIFY=focused python bin/test sympy/printing/tests/test_ccode.py -v',
      '/tmp',
      120,
      false,
      ['test'],
    );

    expect(receipt.verification).toMatchObject({
      status: 'passed', kind: 'test', collected_tests: 30, executed_tests: 30,
    });
  });

  it('records pytest selection counts and rejects evasive focused checks', () => {
    const selected = normalizeExternalToolOutput(
      '/tmp', 'verify-selected', 'bash', 'collected 100 items / 90 deselected / 10 selected\n10 passed', undefined, false, 0,
    );
    attachVerificationReceipt(
      selected, 'NONOKA_VERIFY=focused pytest -q -k api', '/tmp', 30, false,
    );
    expect(selected.verification).toMatchObject({
      status: 'unavailable', collected_tests: 100, executed_tests: 10, deselected_tests: 90,
    });

    const truncated = normalizeExternalToolOutput(
      '/tmp', 'verify-tail', 'bash', '2 passed', undefined, false, 0,
    );
    attachVerificationReceipt(
      truncated, 'NONOKA_VERIFY=focused pytest -q | tail -20', '/tmp', 30, false,
    );
    expect(truncated.verification?.status).toBe('unavailable');

    const custom = normalizeExternalToolOutput(
      '/tmp', 'verify-custom', 'bash', 'custom check', undefined, false, 0,
    );
    attachVerificationReceipt(
      custom, 'NONOKA_VERIFY=focused python -c "print(1)"', '/tmp', 30, false, ['test'],
    );
    expect(custom.verification).toMatchObject({
      status: 'unavailable', kind: 'custom',
    });
  });

  it('marks zero-test and truncated verification as unavailable', () => {
    const zero = normalizeExternalToolOutput(
      '/tmp', 'verify-zero', 'bash', 'no tests ran in 0.01s', undefined, false, 0,
    );
    attachVerificationReceipt(zero, 'pytest -q', '/tmp', undefined, false);
    expect(zero.verification?.status).toBe('unavailable');

    const partial = normalizeExternalToolOutput(
      '/tmp', 'verify-partial', 'bash', 'x'.repeat(PROVIDER_COMPLETE_OBSERVATION_MAX_BYTES),
      undefined, false, 0,
    );
    attachVerificationReceipt(partial, 'NONOKA_VERIFY=focused npm test', '/tmp', undefined, false);
    expect(partial.verification?.status).toBe('unavailable');
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
