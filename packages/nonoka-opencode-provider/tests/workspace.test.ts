import { describe, expect, it } from 'bun:test';
import fs from 'fs';
import os from 'os';
import path from 'path';
import { receiptForWorkspaceResult, recordWorkspaceBefore } from '../src/workspace';

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
});
