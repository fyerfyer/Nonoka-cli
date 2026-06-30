import { describe, expect, it } from "bun:test";
import { NonokaLanguageModel } from "../src/nonoka-language-model";

describe("NonokaLanguageModel", () => {
  it("constructs with the given model id and config", () => {
    const model = new NonokaLanguageModel(
      "deepseek-chat",
      { sessionId: "sess-1" },
      {
        provider: "nonoka",
        serverCommand: ["nonoka-cli", "--server"],
        cwd: ".",
        configPath: "./nonoka.yaml",
        model: "deepseek-chat",
        env: { KEY: "value" },
      },
    );

    expect(model.modelId).toBe("deepseek-chat");
    expect(model.provider).toBe("nonoka");
    expect(model.specificationVersion).toBe("v3");
    expect(model.supportedUrls).toEqual({});
  });

  it("throws when serverCommand is empty", () => {
    const model = new NonokaLanguageModel(
      "x",
      {},
      {
        provider: "nonoka",
        serverCommand: [],
        cwd: ".",
      },
    );

    expect(() => model["spawnServer"]()).toThrow("serverCommand must not be empty");
  });
});
