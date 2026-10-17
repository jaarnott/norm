import { describe, expect, it } from 'vitest';
import { periodFor } from './dates';

describe('report periods', () => {
  it('builds the last-N-days presets ending today', () => {
    const now = new Date('2026-10-12T20:30:00Z');
    expect(periodFor('28d', 'Pacific/Auckland', undefined, now)).toEqual({ key: '28d', from: '2026-09-16', to: '2026-10-13' });
    expect(periodFor('7d', 'Pacific/Auckland', undefined, now)).toEqual({ key: '7d', from: '2026-10-07', to: '2026-10-13' });
    expect(periodFor('custom', 'Pacific/Auckland', { from: '2026-01-01', to: '2026-01-31' }, now))
      .toEqual({ key: 'custom', from: '2026-01-01', to: '2026-01-31' });
  });
});
