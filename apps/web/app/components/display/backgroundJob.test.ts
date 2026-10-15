import { describe, expect, it } from 'vitest';
import { estimateRemaining, formatDuration, isActive, percent, statusLabel } from './backgroundJob';

describe('background job card logic', () => {
  it('reads durations the way people say them', () => {
    expect(formatDuration(42)).toBe('42s');
    expect(formatDuration(75)).toBe('1m 15s');
    expect(formatDuration(3700)).toBe('1h 1m');
    expect(formatDuration(-5)).toBe('0s');
  });

  it('estimates time left only once there is a pace to go on', () => {
    expect(estimateRemaining({ done: 2, total: 200 }, 60)).toBeNull();
    expect(estimateRemaining({ done: 10, total: 30 }, 100)).toBe(200);
    expect(estimateRemaining({ done: 30, total: 30 }, 100)).toBeNull();
  });

  it('shows progress as a percentage of the total', () => {
    expect(percent({ done: 37, total: 212 })).toBe(17);
    expect(percent({ done: 0, total: 0 })).toBe(0);
    expect(percent({ done: 5, total: 4 })).toBe(100);
  });

  it('says what state the job is in', () => {
    expect(statusLabel({ status: 'running', cancel_requested: true, stalled: false })).toBe('Stopping…');
    expect(statusLabel({ status: 'running', cancel_requested: false, stalled: true })).toBe('Picking back up…');
    expect(statusLabel({ status: 'failed', cancel_requested: false, stalled: false })).toBe("Didn't finish");
    expect(isActive('queued') && isActive('running') && !isActive('done')).toBe(true);
  });
});
