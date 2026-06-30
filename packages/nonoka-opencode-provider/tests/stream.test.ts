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
});
