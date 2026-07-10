import { describe, expect, it } from 'bun:test';
import fs from 'fs';
import {
  NonokaLanguageModel,
  getChatSessionIdFile,
  loadChatSessionId,
  saveChatSessionId,
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
});
