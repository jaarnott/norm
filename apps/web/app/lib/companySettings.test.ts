import { describe, it, expect } from 'vitest';
import { FUNCTIONS_MODES, currencyLabel, timeZoneOptions } from './companySettings';

describe('FUNCTIONS_MODES', () => {
  it('offers exactly the modes the API accepts, in order', () => {
    expect(FUNCTIONS_MODES.map((m) => m.id)).toEqual(['off', 'shadow', 'live']);
  });

  it('explains each one in a line', () => {
    for (const m of FUNCTIONS_MODES) {
      expect(m.description.length).toBeGreaterThan(0);
      expect(m.description).not.toContain('\n');
    }
  });
});

describe('timeZoneOptions', () => {
  it('keeps the saved zone when the browser does not list it', () => {
    expect(timeZoneOptions('UTC', ['Pacific/Auckland', 'Australia/Sydney'])).toEqual([
      'Australia/Sydney',
      'Pacific/Auckland',
      'UTC',
    ]);
  });

  it('lists a zone once', () => {
    expect(timeZoneOptions('Pacific/Auckland', ['Pacific/Auckland'])).toEqual(['Pacific/Auckland']);
  });

  it('adds nothing for an empty value', () => {
    expect(timeZoneOptions('', ['Pacific/Auckland'])).toEqual(['Pacific/Auckland']);
  });
});

describe('currencyLabel', () => {
  it('names the currency when the browser can', () => {
    const names = new Intl.DisplayNames(['en'], { type: 'currency' });
    expect(currencyLabel('NZD', names)).toBe('NZD — New Zealand Dollar');
  });

  it('falls back to the code', () => {
    expect(currencyLabel('NZD')).toBe('NZD');
    expect(currencyLabel('XYZ', { of: (c) => c })).toBe('XYZ');
    expect(currencyLabel('NZD', { of: () => { throw new RangeError('bad'); } })).toBe('NZD');
  });
});
