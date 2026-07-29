import type { ProviderV3 } from '@ai-sdk/provider';
import {
  NonokaLanguageModel,
  type NonokaLanguageModelConfig,
  type NonokaLanguageModelSettings,
} from './nonoka-language-model.js';
import type { NonokaVerificationKind } from './protocol.js';

export interface NonokaProviderSettings {
  /**
   * Command used to spawn the nonoka-cli server. Can be a shell-style string
   * or an argv array.
   * @default "nonoka-cli --server"
   */
  serverCommand?: string | string[];

  /**
   * Working directory for the spawned server.
   * @default process.cwd()
   */
  cwd?: string;

  /**
   * Optional nonoka config file path passed to the server.
   */
  configPath?: string;

  /**
   * Optional model override.
   */
  model?: string;

  /** Optional per-turn generation controls forwarded to nonoka-cli. */
  temperature?: number;
  maxTurns?: number;
  timeoutSeconds?: number;
  wallTimeoutSeconds?: number;
  toolBudget?: number;
  maxContextBytes?: number;
  maxExternalResultBytes?: number;
  requireWorkspaceMutation?: boolean;
  requireObservedEffect?: boolean;
  requireFocusedVerification?: boolean;
  verificationEnforcement?: 'strict' | 'advisory';
  maxCompletionCorrections?: number;
  /** Verification categories accepted for completion by this profile. */
  allowedVerificationKinds?: NonokaVerificationKind[];

  /**
   * Environment variables applied to OpenCode-hosted shell tool commands.
   * This is separate from `env`, which configures only the Nonoka server.
   */
  hostShellEnv?: Record<string, string>;

  /**
   * Trusted shell initialization statements run before each hosted shell
   * command, for example activating a benchmark-owned Conda environment.
   */
  hostShellInit?: string[];

  /**
   * Additional environment variables for the server process.
   */
  env?: Record<string, string | undefined>;
}

export interface NonokaProvider extends ProviderV3 {
  (modelId: string, settings?: NonokaLanguageModelSettings): NonokaLanguageModel;
  languageModel(
    modelId: string,
    settings?: NonokaLanguageModelSettings,
  ): NonokaLanguageModel;
}

/**
 * Create a Nonoka provider instance.
 *
 * @example
 * ```ts
 * import { createNonoka } from '@nonoka/opencode-provider';
 *
 * const nonoka = createNonoka({
 *   serverCommand: ['nonoka-cli', '--server'],
 *   cwd: process.cwd(),
 * });
 *
 * const model = nonoka('default');
 * ```
 */
export function createNonoka(
  settings: NonokaProviderSettings = {},
): NonokaProvider {
  const cwd = settings.cwd ?? process.cwd();
  const serverCommand = normalizeServerCommand(
    settings.serverCommand ?? 'nonoka-cli --server',
  );

  const config: NonokaLanguageModelConfig = {
    provider: 'nonoka',
    serverCommand,
    cwd,
    configPath: settings.configPath,
    model: settings.model,
    temperature: settings.temperature,
    maxTurns: settings.maxTurns,
    timeoutSeconds: settings.timeoutSeconds,
    wallTimeoutSeconds: settings.wallTimeoutSeconds,
    toolBudget: settings.toolBudget,
    maxContextBytes: settings.maxContextBytes,
    maxExternalResultBytes: settings.maxExternalResultBytes,
    requireWorkspaceMutation: settings.requireWorkspaceMutation,
    requireObservedEffect: settings.requireObservedEffect,
    requireFocusedVerification: settings.requireFocusedVerification,
    verificationEnforcement: settings.verificationEnforcement,
    maxCompletionCorrections: settings.maxCompletionCorrections,
    allowedVerificationKinds: settings.allowedVerificationKinds,
    hostShellEnv: settings.hostShellEnv,
    hostShellInit: settings.hostShellInit,
    env: settings.env,
  };

  const createChatModel = (
    modelId: string,
    modelSettings: NonokaLanguageModelSettings = {},
  ) => new NonokaLanguageModel(modelId, modelSettings, config);

  const provider = function (
    modelId: string,
    modelSettings?: NonokaLanguageModelSettings,
  ) {
    if (new.target) {
      throw new Error(
        'The model factory function cannot be called with the new keyword.',
      );
    }
    return createChatModel(modelId, modelSettings);
  } as NonokaProvider;

  provider.languageModel = createChatModel;

  return provider;
}

/**
 * Default Nonoka provider instance.
 */
export const nonoka = createNonoka();

function normalizeServerCommand(command: string | string[]): string[] {
  if (Array.isArray(command)) {
    return command;
  }
  return command.split(/\s+/).filter(Boolean);
}

// Re-export for advanced users.
export { NonokaLanguageModel };
export type { NonokaLanguageModelConfig, NonokaLanguageModelSettings };

export default createNonoka;
