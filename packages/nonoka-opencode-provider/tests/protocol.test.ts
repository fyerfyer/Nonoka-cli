import { describe, expect, it } from "bun:test";
import {
  encodeChatRequest,
  encodeCancelMessage,
  parseOutboundLine,
  type ExternalMCPServerDefinition,
  type ExternalSkillDefinition,
  type NonokaChatRequest,
  type NonokaToolCallEvent,
} from "../src/protocol";

describe("encodeChatRequest", () => {
  it("encodes a simple chat request", () => {
    const req: NonokaChatRequest = {
      type: "chat",
      messages: [{ role: "user", content: "hello" }],
      session_id: "sess-1",
      cwd: "/tmp",
      model: "deepseek-chat",
    };
    const line = encodeChatRequest(req);
    const parsed = JSON.parse(line);
    expect(parsed.type).toBe("chat");
    expect(parsed.messages).toEqual([{ role: "user", content: "hello" }]);
    expect(parsed.session_id).toBe("sess-1");
    expect(parsed.cwd).toBe("/tmp");
    expect(parsed.model).toBe("deepseek-chat");
  });

  it("encodes defaults correctly", () => {
    const req: NonokaChatRequest = {
      type: "chat",
      messages: [{ role: "user", content: "hi" }],
      cwd: ".",
    };
    const line = encodeChatRequest(req);
    const parsed = JSON.parse(line);
    expect(parsed.cwd).toBe(".");
    expect(parsed.session_id).toBeUndefined();
    expect(parsed.model).toBeUndefined();
  });

  it("encodes a structured external-tool receipt", () => {
    const req: NonokaChatRequest = {
      type: "chat",
      messages: [{
        role: "tool", content: "done", tool_call_id: "call-1",
        result: { result: "done", workspace: { root: "/tmp", before_digest: "a", after_digest: "b" } },
      }],
      cwd: "/tmp",
    };
    expect(JSON.parse(encodeChatRequest(req)).messages[0].result.workspace.after_digest).toBe("b");
  });
});

describe("parseOutboundLine", () => {
  it("parses text_delta", () => {
    const msg = parseOutboundLine('{"type":"text_delta","text":"hi"}');
    expect(msg?.type).toBe("text_delta");
    expect((msg as any).text).toBe("hi");
  });

  it("parses finish", () => {
    const msg = parseOutboundLine('{"type":"finish","finish_reason":"stop"}');
    expect(msg?.type).toBe("finish");
    expect((msg as any).finish_reason).toBe("stop");
  });

  it("parses error", () => {
    const msg = parseOutboundLine('{"type":"error","message":"boom"}');
    expect(msg?.type).toBe("error");
    expect((msg as any).message).toBe("boom");
  });

  it("parses session_init", () => {
    const msg = parseOutboundLine('{"type":"session_init","session_id":"abc"}');
    expect(msg?.type).toBe("session_init");
    expect((msg as any).session_id).toBe("abc");
  });

  it("returns null for empty lines", () => {
    expect(parseOutboundLine("")).toBeNull();
    expect(parseOutboundLine("   ")).toBeNull();
  });

  it("throws on invalid json", () => {
    expect(() => parseOutboundLine("not json")).toThrow();
  });

  it("parses tool_call with metadata", () => {
    const msg = parseOutboundLine(
      '{"type":"tool_call","tool_call_id":"tc-1","tool_name":"skill__foo__bar","args":{"x":1},"metadata":{"kind":"skill","skill":"foo"}}',
    );
    expect(msg?.type).toBe("tool_call");
    const tc = msg as NonokaToolCallEvent;
    expect(tc.tool_call_id).toBe("tc-1");
    expect(tc.tool_name).toBe("skill__foo__bar");
    expect(tc.metadata).toEqual({ kind: "skill", skill: "foo" });
  });

  it("encodes external mcp servers and skills", () => {
    const mcp: ExternalMCPServerDefinition = {
      name: "memory",
      command: "npx",
      args: ["-y", "@modelcontextprotocol/server-memory"],
      env: { MEMORY_PATH: "/tmp/memory.json" },
    };
    const skill: ExternalSkillDefinition = {
      name: "todo",
      package: "nonoka-skill-todo",
      version: "1.0.0",
      config: { strict: true },
    };
    const req: NonokaChatRequest = {
      type: "chat",
      messages: [{ role: "user", content: "hi" }],
      external_mcp_servers: [mcp],
      external_skills: [skill],
      cwd: "/tmp",
    };
    const line = encodeChatRequest(req);
    const parsed = JSON.parse(line);
    expect(parsed.external_mcp_servers).toEqual([mcp]);
    expect(parsed.external_skills).toEqual([skill]);
  });
});

describe("encodeCancelMessage", () => {
  it("encodes a cancellation request", () => {
    expect(JSON.parse(encodeCancelMessage({ type: "cancel", request_id: "req-1" }))).toEqual({
      type: "cancel", request_id: "req-1",
    });
  });
});
