import { describe, expect, it } from "bun:test";
import { createNonokaStreamTransformer } from "../src/stream";

async function collectStream<T>(
  stream: ReadableStream<T>,
): Promise<T[]> {
  const parts: T[] = [];
  const reader = stream.getReader();
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    parts.push(value);
  }
  return parts;
}

function createInputStream(lines: string[]): ReadableStream<string> {
  return new ReadableStream({
    start(controller) {
      for (const line of lines) {
        controller.enqueue(line);
      }
      controller.close();
    },
  });
}

describe("createNonokaStreamTransformer", () => {
  it("requires a compatible protocol acknowledgement in production mode", async () => {
    const transformer = createNonokaStreamTransformer({ requireProtocolAck: true });
    const input = createInputStream([
      '{"type":"session_init","session_id":"legacy"}',
    ]);
    await expect(collectStream(input.pipeThrough(transformer))).rejects.toThrow(
      "did not acknowledge",
    );
  });

  it("rejects a server that closes before acknowledging the protocol", async () => {
    const transformer = createNonokaStreamTransformer({ requireProtocolAck: true });
    const input = createInputStream([]);
    await expect(collectStream(input.pipeThrough(transformer))).rejects.toThrow(
      "closed before acknowledging",
    );
  });

  it("accepts a compatible protocol acknowledgement", async () => {
    const transformer = createNonokaStreamTransformer({ requireProtocolAck: true });
    const input = createInputStream([
      '{"type":"protocol_ack","version":"1.1","capabilities":["external_tool_receipts","persistent_runtime_limits","termination_reasons","tool_approval_resume","typed_verification_receipts"],"cli_version":"0.2.7","framework_version":"1.3.5"}',
      '{"type":"finish","finish_reason":"stop"}',
    ]);
    const parts = await collectStream(input.pipeThrough(transformer));
    expect(parts[parts.length - 1].type).toBe("finish");
  });

  it("emits text-delta parts", async () => {
    const transformer = createNonokaStreamTransformer();
    const input = createInputStream([
      '{"type":"text_delta","text":"hello"}',
      '{"type":"text_delta","text":" world"}',
      '{"type":"finish","finish_reason":"stop"}',
    ]);

    const parts = await collectStream(input.pipeThrough(transformer));

    expect(parts.length).toBeGreaterThanOrEqual(3);
    expect(parts[0].type).toBe("text-start");
    expect(parts[1].type).toBe("text-delta");
    expect((parts[1] as any).delta).toBe("hello");
    expect(parts[2].type).toBe("text-delta");
    expect((parts[2] as any).delta).toBe(" world");

    const finish = parts[parts.length - 1];
    expect(finish.type).toBe("finish");
    expect((finish as any).finishReason.unified).toBe("stop");
  });

  it("forwards reported runtime token usage", async () => {
    const transformer = createNonokaStreamTransformer();
    const input = createInputStream([
      '{"type":"finish","finish_reason":"stop","runtime":{"usage":{"input_tokens":12,"output_tokens":7}}}',
    ]);

    const parts = await collectStream(input.pipeThrough(transformer));
    const finish = parts[parts.length - 1] as any;

    expect(finish.usage.inputTokens.total).toBe(12);
    expect(finish.usage.outputTokens.total).toBe(7);
  });

  it("keeps token usage unknown when the model backend omits it", async () => {
    const transformer = createNonokaStreamTransformer();
    const input = createInputStream([
      '{"type":"finish","finish_reason":"stop","runtime":{"usage":{"input_tokens":0,"output_tokens":0}}}',
    ]);

    const parts = await collectStream(input.pipeThrough(transformer));
    const finish = parts[parts.length - 1] as any;

    expect(finish.usage.inputTokens.total).toBeUndefined();
    expect(finish.usage.outputTokens.total).toBeUndefined();
  });

  it("emits error part", async () => {
    const transformer = createNonokaStreamTransformer();
    const input = createInputStream([
      '{"type":"text_delta","text":"partial"}',
      '{"type":"error","message":"failed"}',
    ]);

    const parts = await collectStream(input.pipeThrough(transformer));
    const error = parts[parts.length - 1];
    expect(error.type).toBe("error");
    expect((error as any).error).toBe("failed");
  });

  it("ignores session_init", async () => {
    const transformer = createNonokaStreamTransformer();
    const input = createInputStream(['{"type":"session_init","session_id":"abc"}']);
    const parts = await collectStream(input.pipeThrough(transformer));
    expect(parts).toHaveLength(0);
  });

  it("consumes tool-call progress without producing a model stream part", async () => {
    const transformer = createNonokaStreamTransformer();
    const input = createInputStream([
      '{"type":"tool_call_progress","tool_call_index":0,"tool_name":"write","argument_chars":2048}',
      '{"type":"finish","finish_reason":"tool_calls"}',
    ]);

    const parts = await collectStream(input.pipeThrough(transformer));

    expect(parts).toHaveLength(1);
    expect(parts[0].type).toBe("finish");
  });

  it("forwards tool_call metadata on allowed tools", async () => {
    const transformer = createNonokaStreamTransformer({
      allowedToolNames: new Set(["skill__foo__bar"]),
    });
    const input = createInputStream([
      '{"type":"tool_call","tool_call_id":"tc-1","tool_name":"skill__foo__bar","args":{"x":1},"metadata":{"kind":"skill","skill":"foo"}}',
      '{"type":"finish","finish_reason":"tool_calls"}',
    ]);
    const parts = await collectStream(input.pipeThrough(transformer));
    const toolCall = parts.find((p: any) => p.type === "tool-call");
    expect(toolCall).toBeDefined();
    expect((toolCall as any).toolName).toBe("skill__foo__bar");
    expect((toolCall as any).metadata).toEqual({ kind: "skill", skill: "foo" });
  });

});
