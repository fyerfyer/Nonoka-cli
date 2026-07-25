import fs from 'fs';
import path from 'path';

export type WorkspaceEffectEvidence = {
  schema_version: 1;
  kind: 'workspace_effect';
  source: string;
  tool_call_id: string;
  tool_name: string;
  changed: boolean;
  created: string[];
  modified: string[];
  deleted: string[];
  policy_violations?: string[];
  restored_paths?: string[];
  before_digest: string;
  after_digest: string;
};

export type TaskEffectEvidence = {
  schema_version: 1;
  kind: 'task_effect';
  source: string;
  tool_call_id: string;
  tool_name: string;
  changed: boolean;
  scope: string;
  collector: string;
  summary?: string;
};

export function appendRunEvidence(event: WorkspaceEffectEvidence | TaskEffectEvidence): void {
  const target = process.env.NONOKA_RUN_EVIDENCE_PATH;
  if (!target) return;
  try {
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.appendFileSync(target, `${JSON.stringify(event)}\n`, 'utf8');
  } catch {
    // Evidence is diagnostic and must not break host tool rendering.
  }
}
