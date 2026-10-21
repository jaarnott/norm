import { describe, it, expect } from 'vitest';
import type { ToolCallRecord } from '../types';
import { changesIn, statusesOf } from './liveChanges';

function call(over: Partial<ToolCallRecord>): ToolCallRecord {
  return {
    id: 't1', iteration: 1, tool_name: '', connector_name: 'norm', action: 'save_app',
    method: 'POST', input_params: {}, status: 'executed', result_payload: {},
    slimmed_content: null, error_message: null, duration_ms: 1, rendered_request: null,
    created_at: '2026-10-21T08:00:00Z',
    ...over,
  };
}

describe('changesIn', () => {
  it('names the app Norm saved in this turn', () => {
    const saved = call({ result_payload: { slug: 'lott-cafe-dashboard', version: 7 } });
    expect(changesIn([saved], new Map())).toEqual([{ kind: 'app', detail: { slug: 'lott-cafe-dashboard' } }]);
  });

  it('ignores what had already run before the turn', () => {
    const old = call({ id: 'old', result_payload: { slug: 'a' } });
    const fresh = call({ id: 'new', result_payload: { slug: 'b' } });
    const before = statusesOf([old]);
    expect(changesIn([old, fresh], before).map(c => c.detail)).toEqual([{ slug: 'b' }]);
  });

  it('counts a change approved on a card: waiting before, run now', () => {
    const waiting = call({
      id: 'c', connector_name: 'norm_reports', action: 'manage_dashboard', status: 'pending_approval',
      input_params: { op: 'rename', dashboard_id: 'd1' },
    });
    const before = statusesOf([waiting]);
    const ran = { ...waiting, status: 'executed' as const, result_payload: { dashboard_id: 'd1', agent: 'reports' } };
    expect(changesIn([ran], before)).toEqual([
      { kind: 'dashboard', detail: { dashboardId: 'd1', agent: 'reports' } },
    ]);
  });

  it('skips a change that was refused, and other tools', () => {
    const refused = call({ status: 'failed', result_payload: { slug: 'a' } });
    const read = call({ id: 'r', action: 'get_app', result_payload: { slug: 'a' } });
    expect(changesIn([refused, read], new Map())).toEqual([]);
  });
});
