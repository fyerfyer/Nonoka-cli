import { createHash } from 'crypto';
import { existsSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from 'fs';
import path from 'path';

const EXCLUDED = new Set(['.git', '.nonoka', 'node_modules', '.venv', 'venv', '__pycache__']);
const MUTATING_TOOLS = new Set(['bash', 'write', 'edit', 'delete', 'apply_patch', 'write_file', 'edit_file', 'delete_file', 'execute_command']);

type Snapshot = { root: string; digest: string; files: Map<string, string> };

function statePath(cwd: string, toolCallId: string): string {
  const key = createHash('sha256').update(`${path.resolve(cwd)}:${toolCallId}`).digest('hex');
  return path.join('/tmp', `nonoka-workspace-${key}.json`);
}

function walk(root: string, current: string, entries: string[]): void {
  for (const entry of readdirSync(current, { withFileTypes: true })) {
    if (EXCLUDED.has(entry.name)) continue;
    const absolute = path.join(current, entry.name);
    const relative = path.relative(root, absolute);
    if (entry.isDirectory()) {
      walk(root, absolute, entries);
    } else if (entry.isFile()) {
      const data = readFileSync(absolute);
      entries.push(`${relative}\0${createHash('sha256').update(data).digest('hex')}`);
    }
  }
}

function snapshot(cwd: string): Snapshot {
  const root = path.resolve(cwd);
  const entries: string[] = [];
  if (existsSync(root)) walk(root, root, entries);
  entries.sort();
  const files = new Map<string, string>(entries.map((entry): [string, string] => {
    const separator = entry.indexOf('\0');
    return [entry.slice(0, separator), entry.slice(separator + 1)];
  }));
  return { root, digest: createHash('sha256').update(entries.join('\n')).digest('hex'), files };
}

function persisted(snapshotValue: Snapshot): Record<string, unknown> {
  return { root: snapshotValue.root, digest: snapshotValue.digest, files: [...snapshotValue.files.entries()] };
}

function restored(value: Record<string, unknown>): Snapshot | undefined {
  if (typeof value.root !== 'string' || typeof value.digest !== 'string' || !Array.isArray(value.files)) return undefined;
  return { root: value.root, digest: value.digest, files: new Map(value.files as [string, string][]) };
}

export function isWorkspaceMutating(toolName: string): boolean {
  return MUTATING_TOOLS.has(toolName.toLowerCase());
}

export function recordWorkspaceBefore(cwd: string, toolCallId: string, toolName: string): void {
  if (!toolCallId || !isWorkspaceMutating(toolName)) return;
  try {
    writeFileSync(statePath(cwd, toolCallId), JSON.stringify(persisted(snapshot(cwd))), 'utf-8');
  } catch {
    // Workspace auditing must not prevent a host tool from rendering.
  }
}

export function receiptForWorkspaceResult(
  cwd: string,
  toolCallId: string,
  toolName: string,
  result: unknown,
): unknown {
  if (!isWorkspaceMutating(toolName)) return result;
  try {
    const state = statePath(cwd, toolCallId);
    const beforeRaw = existsSync(state) ? JSON.parse(readFileSync(state, 'utf-8')) : undefined;
    const before = beforeRaw && restored(beforeRaw);
    const after = snapshot(cwd);
    if (existsSync(state)) rmSync(state, { force: true });
    if (!before) return result;
    const created = [...after.files.keys()].filter((file) => !before.files.has(file));
    const deleted = [...before.files.keys()].filter((file) => !after.files.has(file));
    const modified = [...after.files.keys()].filter((file) => before.files.has(file) && before.files.get(file) !== after.files.get(file));
    return {
      result,
      host: 'opencode',
      workspace: {
        root: after.root,
        before_digest: before.digest,
        after_digest: after.digest,
        created,
        modified,
        deleted,
        collector: 'nonoka-opencode-provider',
      },
    };
  } catch {
    return result;
  }
}
