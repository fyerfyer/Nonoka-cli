import { describe, expect, it } from 'bun:test';
import fs from 'fs';
import os from 'os';
import path from 'path';
import {
  externalTargetsForAction,
  looksStatefulHostAction,
  receiptForWorkspaceResult,
  recordWorkspaceBefore,
} from '../src/workspace';

describe('workspace receipts', () => {
  it('records a credential-free workspace attestation for a write tool', () => {
    const cwd = fs.mkdtempSync(path.join(os.tmpdir(), 'nonoka-workspace-'));
    try {
      fs.writeFileSync(path.join(cwd, 'before.txt'), 'before');
      recordWorkspaceBefore(cwd, 'call-1', 'write');
      fs.writeFileSync(path.join(cwd, 'before.txt'), 'after');
      fs.writeFileSync(path.join(cwd, 'created.txt'), 'created');
      const receipt: any = receiptForWorkspaceResult(cwd, 'call-1', 'write', 'ok');
      expect(receipt.result).toBe('ok');
      expect(receipt.workspace.modified).toEqual(['before.txt']);
      expect(receipt.workspace.created).toEqual(['created.txt']);
    } finally {
      fs.rmSync(cwd, { recursive: true, force: true });
    }
  });

  it('observes effects for arbitrary host tool names instead of inferring by name', () => {
    const cwd = fs.mkdtempSync(path.join(os.tmpdir(), 'nonoka-workspace-'));
    const evidence = path.join(cwd, 'evidence.ndjson');
    const previous = process.env.NONOKA_RUN_EVIDENCE_PATH;
    process.env.NONOKA_RUN_EVIDENCE_PATH = evidence;
    try {
      recordWorkspaceBefore(cwd, 'call-custom', 'database_query');
      fs.writeFileSync(path.join(cwd, 'changed.txt'), 'changed');
      const receipt: any = receiptForWorkspaceResult(
        cwd, 'call-custom', 'database_query', { result: 'ok' },
      );
      expect(receipt.workspace.created).toEqual(['changed.txt']);
      const events = fs.readFileSync(evidence, 'utf8').trim().split('\n').map(JSON.parse);
      const event = events.find((value) => value.kind === 'workspace_effect');
      expect(event.changed).toBe(true);
      expect(event.tool_name).toBe('database_query');
    } finally {
      if (previous === undefined) delete process.env.NONOKA_RUN_EVIDENCE_PATH;
      else process.env.NONOKA_RUN_EVIDENCE_PATH = previous;
      fs.rmSync(cwd, { recursive: true, force: true });
    }
  });

  it('attests a successful stateful system action without forcing a cwd file', () => {
    const cwd = fs.mkdtempSync(path.join(os.tmpdir(), 'nonoka-workspace-'));
    try {
      recordWorkspaceBefore(cwd, 'call-system', 'bash');
      const receipt: any = receiptForWorkspaceResult(
        cwd,
        'call-system',
        'bash',
        { result: 'configured', exit_code: 0 },
        { command: 'systemctl enable example.service' },
      );
      expect(receipt.workspace.created).toEqual([]);
      expect(receipt.effect).toMatchObject({
        changed: true,
        scope: 'external',
        collector: 'successful-host-action',
      });
    } finally {
      fs.rmSync(cwd, { recursive: true, force: true });
    }
  });

  it('attests a changed external target even when the host omits an exit code', () => {
    const cwd = fs.mkdtempSync(path.join(os.tmpdir(), 'nonoka-workspace-'));
    const externalRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'nonoka-external-'));
    const target = path.join(externalRoot, 'service.conf');
    try {
      recordWorkspaceBefore(cwd, 'call-external', 'bash', {
        command: `printf enabled > ${target}`,
      });
      fs.writeFileSync(target, 'enabled');
      const receipt: any = receiptForWorkspaceResult(
        cwd,
        'call-external',
        'bash',
        { result: 'configured' },
        { command: `printf enabled > ${target}` },
      );
      expect(receipt.effect).toMatchObject({
        changed: true,
        scope: 'external',
        collector: 'external-target-snapshot',
      });
    } finally {
      fs.rmSync(cwd, { recursive: true, force: true });
      fs.rmSync(externalRoot, { recursive: true, force: true });
    }
  });

  it('keeps external target collection bounded and excludes virtual system roots', () => {
    const cwd = fs.mkdtempSync(path.join(os.tmpdir(), 'nonoka-workspace-'));
    try {
      const command = [
        'mkdir -p /proc/unsafe /sys/unsafe /dev/unsafe /',
        ...Array.from({ length: 12 }, (_, index) => `/tmp/nonoka-target-${index}`),
      ].join(' ');
      const targets = externalTargetsForAction(cwd, 'bash', { command });
      expect(targets).toHaveLength(8);
      expect(targets.every((target) => target.startsWith('/tmp/nonoka-target-'))).toBe(true);
    } finally {
      fs.rmSync(cwd, { recursive: true, force: true });
    }
  });

  it('does not classify read-only shell commands as stateful effects', () => {
    expect(looksStatefulHostAction('bash', { command: 'find . -type f 2>/dev/null' })).toBe(false);
    expect(looksStatefulHostAction('bash', { command: 'curl -s http://localhost:8080/health' })).toBe(false);
    expect(looksStatefulHostAction('bash', { command: 'mkdir -p /srv/example' })).toBe(true);
  });

  it('attests an unchanged workspace for read-only effects', () => {
    const cwd = fs.mkdtempSync(path.join(os.tmpdir(), 'nonoka-workspace-'));
    try {
      fs.writeFileSync(path.join(cwd, 'input.txt'), 'input');
      recordWorkspaceBefore(cwd, 'call-read', 'unknown_reader');
      const receipt: any = receiptForWorkspaceResult(cwd, 'call-read', 'unknown_reader', 'input');
      expect(receipt.workspace.before_digest).toBe(receipt.workspace.after_digest);
      expect(receipt.workspace.created).toEqual([]);
      expect(receipt.workspace.modified).toEqual([]);
      expect(receipt.workspace.deleted).toEqual([]);
    } finally {
      fs.rmSync(cwd, { recursive: true, force: true });
    }
  });

  it('restores files protected by filesystem permissions and reports the violation', () => {
    const cwd = fs.mkdtempSync(path.join(os.tmpdir(), 'nonoka-workspace-'));
    try {
      const protectedFile = path.join(cwd, 'fixture.db');
      fs.writeFileSync(protectedFile, 'original');
      fs.chmodSync(protectedFile, 0o444);
      recordWorkspaceBefore(cwd, 'call-policy', 'arbitrary_command');

      fs.chmodSync(protectedFile, 0o644);
      fs.writeFileSync(protectedFile, 'modified');
      const receipt: any = receiptForWorkspaceResult(
        cwd, 'call-policy', 'arbitrary_command', 'command completed',
      );

      expect(fs.readFileSync(protectedFile, 'utf8')).toBe('original');
      expect(fs.statSync(protectedFile).mode & 0o222).toBe(0);
      expect(receipt.workspace.policy_violations).toEqual(['fixture.db']);
      expect(receipt.workspace.restored_paths).toEqual(['fixture.db']);
      expect(receipt.workspace.before_digest).toBe(receipt.workspace.after_digest);
    } finally {
      fs.chmodSync(path.join(cwd, 'fixture.db'), 0o644);
      fs.rmSync(cwd, { recursive: true, force: true });
    }
  });

  it('preserves output artifact metadata while adding workspace attestation', () => {
    const cwd = fs.mkdtempSync(path.join(os.tmpdir(), 'nonoka-workspace-'));
    try {
      recordWorkspaceBefore(cwd, 'call-2', 'write');
      fs.writeFileSync(path.join(cwd, 'created.txt'), 'created');
      const receipt: any = receiptForWorkspaceResult(cwd, 'call-2', 'write', {
        result: 'preview', artifact_ref: '/tmp/full.txt', original_bytes: 1000, truncated: true,
      });
      expect(receipt.result).toBe('preview');
      expect(receipt.artifact_ref).toBe('/tmp/full.txt');
      expect(receipt.truncated).toBe(true);
      expect(receipt.workspace.created).toEqual(['created.txt']);
    } finally {
      fs.rmSync(cwd, { recursive: true, force: true });
    }
  });
});
