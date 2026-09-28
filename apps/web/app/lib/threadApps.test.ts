import { describe, it, expect } from 'vitest';
import { threadMembers, threadLabel, threadAccent } from './threadApps';

const bamboo = { slug: 'bamboohr-app', name: 'BambooHR', member: 'hr' };
const reportsApp = { slug: 'loaded-reports', name: 'Loaded Reports', member: 'reports' };

describe('threadMembers', () => {
  it('files a thread under every member whose App it used', () => {
    expect(threadMembers({ domain: 'norm', apps: [bamboo, reportsApp] })).toEqual(['hr', 'reports']);
  });

  it('keeps a legacy thread under its old agent', () => {
    expect(threadMembers({ domain: 'procurement' })).toEqual(['procurement']);
  });

  it('files a Norm thread that used no App under no member', () => {
    expect(threadMembers({ domain: 'norm', apps: [] })).toEqual([]);
    expect(threadMembers({ domain: 'meta' })).toEqual([]);
  });
});

describe('threadLabel', () => {
  it('names the Apps used', () => {
    expect(threadLabel({ domain: 'norm', apps: [bamboo, reportsApp] })).toBe('BambooHR · Loaded Reports');
  });

  it('falls back to the legacy agent, then Norm', () => {
    expect(threadLabel({ domain: 'time_attendance' })).toBe('time attendance');
    expect(threadLabel({ domain: 'norm' })).toBe('Norm');
    expect(threadLabel({ domain: 'unknown' })).toBe('Norm');
  });
});

describe('threadAccent', () => {
  it('wears its first member, else Norm', () => {
    expect(threadAccent({ domain: 'norm', apps: [reportsApp, bamboo] })).toBe('reports');
    expect(threadAccent({ domain: 'norm' })).toBe('norm');
  });
});
