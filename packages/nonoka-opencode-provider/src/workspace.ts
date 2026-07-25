import { createHash } from 'crypto';
import {
  chmodSync,
  constants,
  copyFileSync,
  existsSync,
  lstatSync,
  mkdirSync,
  readdirSync,
  readFileSync,
  readlinkSync,
  rmSync,
  statSync,
  writeFileSync,
} from 'fs';
import path from 'path';
import { appendRunEvidence } from './evidence.js';

const EXCLUDED = new Set(['.git', '.nonoka', 'node_modules', '.venv', 'venv', '__pycache__']);

const MUTATION_TOOLS = new Set([
  'write', 'write_file', 'edit', 'edit_file', 'apply_patch', 'delete_file',
]);

const MAX_EXTERNAL_TARGETS = 8;
const MAX_DIRECTORY_ENTRIES = 64;
const MAX_HASH_BYTES = 64 * 1024;
const EXCLUDED_EXTERNAL_ROOTS = ['/dev', '/proc', '/sys'];

type ExternalTargetSnapshot = {
  path: string;
  exists: boolean;
  kind?: 'file' | 'directory' | 'symlink' | 'other';
  size?: number;
  mtime_ms?: number;
  digest?: string;
};

/** Conservatively classify shell syntax that is intended to alter durable
 * host state. This is Adapter evidence only: core never parses commands, and
 * the classifier contains no benchmark task names, paths, or data patterns. */
export function looksStatefulHostAction(
  toolName: string,
  toolArguments: Record<string, unknown> | undefined,
): boolean {
  const normalizedTool = toolName.toLowerCase();
  if (MUTATION_TOOLS.has(normalizedTool)) return true;
  if (!['bash', 'terminal', 'execute_command'].includes(normalizedTool)) return false;
  const command = toolArguments?.command;
  if (typeof command !== 'string' || command.length === 0) return false;
  const value = command.toLowerCase();
  if (/(?:^|[;&|]\s*)(?:mkdir|rmdir|rm|mv|cp|touch|chmod|chown|ln|install|truncate|dd|mount|umount|systemctl|service|kill|pkill|nohup)\b/m.test(value)) {
    return true;
  }
  if (/\b(?:apt|apt-get|apk|dnf|yum|pip|pip3|npm|pnpm|yarn|bun|cargo)\s+(?:install|add|remove|uninstall|update|upgrade)\b/.test(value)) {
    return true;
  }
  if (/\bgit\s+(?:init|clone|checkout|switch|add|commit|push|reset|clean|apply|am|merge|rebase|tag|branch)\b/.test(value)) {
    return true;
  }
  if (/\bsed\s+[^;&|\n]*-[a-z]*i\b/.test(value) || /(?:^|[;&|]\s*)tee\b/m.test(value)) {
    return true;
  }
  return /(?<![0-9])>{1,2}(?!\s*(?:\/dev\/null|&?[0-9]))/.test(value);
}

function isAllowedExternalTarget(candidate: string, cwd: string): boolean {
  if (!path.isAbsolute(candidate)) return false;
  const resolved = path.resolve(candidate);
  if (resolved === path.parse(resolved).root) return false;
  if (resolved === cwd || resolved.startsWith(`${cwd}${path.sep}`)) return false;
  return !EXCLUDED_EXTERNAL_ROOTS.some(
    (root) => resolved === root || resolved.startsWith(`${root}${path.sep}`),
  );
}

/** Extract a small, bounded set of explicit absolute-path targets from a host
 * tool call. This is intentionally syntax-light: it does not attempt to parse
 * a shell or infer task semantics, and it never recursively scans a system
 * root. */
export function externalTargetsForAction(
  cwd: string,
  toolName: string,
  toolArguments: Record<string, unknown> | undefined,
): string[] {
  if (!looksStatefulHostAction(toolName, toolArguments)) return [];
  const strings: string[] = [];
  const collect = (value: unknown, depth = 0): void => {
    if (strings.length >= MAX_EXTERNAL_TARGETS * 4 || depth > 3) return;
    if (typeof value === 'string') {
      strings.push(value);
    } else if (Array.isArray(value)) {
      for (const item of value) collect(item, depth + 1);
    } else if (value && typeof value === 'object') {
      for (const item of Object.values(value as Record<string, unknown>)) collect(item, depth + 1);
    }
  };
  collect(toolArguments);

  const resolvedCwd = path.resolve(cwd);
  const targets = new Set<string>();
  for (const value of strings) {
    const matches = value.match(/\/[^^\s"'`;|&<>()[\]{}]+/g) ?? [];
    for (const raw of matches) {
      const candidate = raw.replace(/[,:]+$/, '');
      if (!isAllowedExternalTarget(candidate, resolvedCwd)) continue;
      targets.add(path.resolve(candidate));
      if (targets.size >= MAX_EXTERNAL_TARGETS) return [...targets];
    }
  }
  return [...targets];
}

function snapshotExternalTarget(target: string): ExternalTargetSnapshot {
  try {
    const stat = lstatSync(target);
    let kind: ExternalTargetSnapshot['kind'] = 'other';
    let digest: string | undefined;
    if (stat.isFile()) {
      kind = 'file';
      if (stat.size <= MAX_HASH_BYTES) {
        digest = createHash('sha256').update(readFileSync(target)).digest('hex');
      }
    } else if (stat.isDirectory()) {
      kind = 'directory';
      const entries = readdirSync(target, { withFileTypes: true })
        .slice(0, MAX_DIRECTORY_ENTRIES)
        .map((entry) => `${entry.name}:${entry.isDirectory() ? 'd' : entry.isFile() ? 'f' : 'o'}`)
        .sort();
      digest = createHash('sha256').update(entries.join('\n')).digest('hex');
    } else if (stat.isSymbolicLink()) {
      kind = 'symlink';
      digest = createHash('sha256').update(readlinkSync(target)).digest('hex');
    }
    return {
      path: target,
      exists: true,
      kind,
      size: stat.size,
      mtime_ms: stat.mtimeMs,
      digest,
    };
  } catch {
    return { path: target, exists: false };
  }
}

type Snapshot = {
  root: string;
  digest: string;
  files: Map<string, string>;
  modes: Map<string, number>;
  protected: Set<string>;
  externalTargets: ExternalTargetSnapshot[];
};

function statePath(cwd: string, toolCallId: string): string {
  const key = createHash('sha256').update(`${path.resolve(cwd)}:${toolCallId}`).digest('hex');
  return path.join('/tmp', `nonoka-workspace-${key}.json`);
}

function backupPath(cwd: string, toolCallId: string): string {
  return statePath(cwd, toolCallId).replace(/\.json$/, '-protected');
}

function walk(
  root: string,
  current: string,
  entries: string[],
  modes: Map<string, number>,
  protectedFiles: Set<string>,
): void {
  for (const entry of readdirSync(current, { withFileTypes: true })) {
    if (EXCLUDED.has(entry.name)) continue;
    const absolute = path.join(current, entry.name);
    const relative = path.relative(root, absolute);
    if (entry.isDirectory()) {
      walk(root, absolute, entries, modes, protectedFiles);
    } else if (entry.isFile()) {
      const data = readFileSync(absolute);
      const mode = statSync(absolute).mode;
      modes.set(relative, mode);
      if ((mode & 0o222) === 0) protectedFiles.add(relative);
      entries.push(`${relative}\0${createHash('sha256').update(data).digest('hex')}`);
    }
  }
}

function snapshot(cwd: string, externalTargets: string[] = []): Snapshot {
  const root = path.resolve(cwd);
  const entries: string[] = [];
  const modes = new Map<string, number>();
  const protectedFiles = new Set<string>();
  if (existsSync(root)) walk(root, root, entries, modes, protectedFiles);
  entries.sort();
  const files = new Map<string, string>(entries.map((entry): [string, string] => {
    const separator = entry.indexOf('\0');
    return [entry.slice(0, separator), entry.slice(separator + 1)];
  }));
  return {
    root,
    digest: createHash('sha256').update(entries.join('\n')).digest('hex'),
    files,
    modes,
    protected: protectedFiles,
    externalTargets: externalTargets.map(snapshotExternalTarget),
  };
}

function persisted(snapshotValue: Snapshot): Record<string, unknown> {
  return {
    root: snapshotValue.root,
    digest: snapshotValue.digest,
    files: [...snapshotValue.files.entries()],
    modes: [...snapshotValue.modes.entries()],
    protected: [...snapshotValue.protected],
    external_targets: snapshotValue.externalTargets,
  };
}

function restored(value: Record<string, unknown>): Snapshot | undefined {
  if (typeof value.root !== 'string' || typeof value.digest !== 'string' || !Array.isArray(value.files)) return undefined;
  return {
    root: value.root,
    digest: value.digest,
    files: new Map(value.files as [string, string][]),
    modes: new Map(Array.isArray(value.modes) ? value.modes as [string, number][] : []),
    protected: new Set(Array.isArray(value.protected) ? value.protected as string[] : []),
    externalTargets: Array.isArray(value.external_targets)
      ? value.external_targets as ExternalTargetSnapshot[]
      : [],
  };
}

function preserveProtectedFiles(value: Snapshot, directory: string): void {
  for (const relative of value.protected) {
    const target = path.join(directory, relative);
    mkdirSync(path.dirname(target), { recursive: true });
    copyFileSync(path.join(value.root, relative), target, constants.COPYFILE_FICLONE);
  }
}

function restoreProtectedFiles(
  before: Snapshot,
  observed: Snapshot,
  directory: string,
): { violations: string[]; restored: string[] } {
  const violations = [...before.protected].filter(
    (relative) => !observed.files.has(relative) || observed.files.get(relative) !== before.files.get(relative),
  );
  const restored: string[] = [];
  for (const relative of violations) {
    const backup = path.join(directory, relative);
    if (!existsSync(backup)) continue;
    const target = path.join(before.root, relative);
    mkdirSync(path.dirname(target), { recursive: true });
    copyFileSync(backup, target, constants.COPYFILE_FICLONE);
    const mode = before.modes.get(relative);
    if (mode !== undefined) chmodSync(target, mode);
    restored.push(relative);
  }
  return { violations, restored };
}

export function recordWorkspaceBefore(
  cwd: string,
  toolCallId: string,
  toolName: string,
  toolArguments?: Record<string, unknown>,
): void {
  if (!toolCallId) return;
  try {
    const targets = externalTargetsForAction(cwd, toolName, toolArguments);
    const before = snapshot(cwd, targets);
    const backup = backupPath(cwd, toolCallId);
    rmSync(backup, { recursive: true, force: true });
    preserveProtectedFiles(before, backup);
    writeFileSync(statePath(cwd, toolCallId), JSON.stringify(persisted(before)), 'utf-8');
  } catch {
    // Workspace auditing must not prevent a host tool from rendering.
  }
}

export function receiptForWorkspaceResult(
  cwd: string,
  toolCallId: string,
  toolName: string,
  result: unknown,
  toolArguments?: Record<string, unknown>,
): unknown {
  try {
    const state = statePath(cwd, toolCallId);
    const beforeRaw: unknown = existsSync(state) ? JSON.parse(readFileSync(state, 'utf-8')) : undefined;
    const before = beforeRaw && typeof beforeRaw === 'object'
      ? restored(beforeRaw as Record<string, unknown>)
      : undefined;
    const backup = backupPath(cwd, toolCallId);
    if (!before) {
      if (existsSync(state)) rmSync(state, { force: true });
      rmSync(backup, { recursive: true, force: true });
      return result;
    }
    const externalTargets = before.externalTargets.map((target) => target.path);
    const observed = snapshot(cwd, externalTargets);
    const policy = restoreProtectedFiles(before, observed, backup);
    const after = policy.restored.length > 0 ? snapshot(cwd, externalTargets) : observed;
    if (existsSync(state)) rmSync(state, { force: true });
    rmSync(backup, { recursive: true, force: true });
    const created = [...after.files.keys()].filter((file) => !before.files.has(file));
    const deleted = [...before.files.keys()].filter((file) => !after.files.has(file));
    const modified = [...after.files.keys()].filter((file) => before.files.has(file) && before.files.get(file) !== after.files.get(file));
    const workspaceChanged = before.digest !== after.digest;
    appendRunEvidence({
      schema_version: 1,
      kind: 'workspace_effect',
      source: 'nonoka-opencode-provider',
      tool_call_id: toolCallId,
      tool_name: toolName,
      changed: workspaceChanged,
      created,
      modified,
      deleted,
      policy_violations: policy.violations,
      restored_paths: policy.restored,
      before_digest: before.digest,
      after_digest: after.digest,
    });
    const receipt = (
      typeof result === 'object' && result !== null && !Array.isArray(result)
        ? result as Record<string, unknown>
        : { result }
    );
    const exitCode = typeof receipt.exit_code === 'number' ? receipt.exit_code : undefined;
    const externalChanged = before.externalTargets.some((target, index) => (
      JSON.stringify(target) !== JSON.stringify(after.externalTargets[index])
    ));
    const successfulStatefulAction = exitCode === 0 && looksStatefulHostAction(toolName, toolArguments);
    const effectChanged = workspaceChanged || externalChanged || successfulStatefulAction;
    const effectScope = workspaceChanged ? 'workspace' : 'external';
    if (effectChanged) {
      appendRunEvidence({
        schema_version: 1,
        kind: 'task_effect',
        source: 'nonoka-opencode-provider',
        tool_call_id: toolCallId,
        tool_name: toolName,
        changed: true,
        scope: effectScope,
        collector: workspaceChanged
          ? 'workspace-snapshot'
          : externalChanged ? 'external-target-snapshot' : 'successful-host-action',
        summary: workspaceChanged
          ? 'The task workspace changed.'
          : externalChanged
            ? 'A bounded external target changed.'
            : 'A successful stateful host action was observed outside the task workspace.',
      });
    }
    return {
      ...receipt,
      host: 'opencode',
      workspace: {
        root: after.root,
        before_digest: before.digest,
        after_digest: after.digest,
        created,
        modified,
        deleted,
        policy_violations: policy.violations,
        restored_paths: policy.restored,
        collector: 'nonoka-opencode-provider',
      },
      effect: {
        changed: effectChanged,
        scope: effectScope,
        collector: workspaceChanged
          ? 'workspace-snapshot'
          : externalChanged ? 'external-target-snapshot' : 'successful-host-action',
        summary: effectChanged
          ? (workspaceChanged
            ? 'The task workspace changed.'
            : externalChanged
              ? 'A bounded external target changed.'
              : 'A successful stateful host action was observed outside the task workspace.')
          : undefined,
      },
    };
  } catch {
    return result;
  }
}
