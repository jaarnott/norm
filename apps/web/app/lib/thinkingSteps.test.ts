import { describe, it, expect } from 'vitest';
import { parseThinkingStep } from './thinkingSteps';

const TS = '2026-10-08T01:23:45.678901+00:00';

describe('parseThinkingStep', () => {
  it('leaves a plain step alone', () => {
    expect(parseThinkingStep('Fetching sales from loadedhub…')).toEqual({
      kind: 'step', text: 'Fetching sales from loadedhub…',
    });
  });

  it('takes the time off a saved status step', () => {
    expect(parseThinkingStep(`[ts:${TS}] Fetching sales from loadedhub…`)).toEqual({
      time: TS, kind: 'step', text: 'Fetching sales from loadedhub…',
    });
  });

  it('reads a saved reasoning step (ts first, as tool_loop writes it)', () => {
    expect(parseThinkingStep(`[ts:${TS}] [reasoning] Let me check last week first.`)).toEqual({
      time: TS, kind: 'reasoning', text: 'Let me check last week first.',
    });
  });

  it('reads a saved test-run step', () => {
    expect(parseThinkingStep(`[ts:${TS}] [test] Would execute update_shift on loadedhub (simulated)`)).toEqual({
      time: TS, kind: 'test', text: 'Would execute update_shift on loadedhub (simulated)',
    });
  });

  it('reads a live test step, which streams without a time', () => {
    expect(parseThinkingStep('[test] Would execute publish_roster on loadedhub (simulated)')).toEqual({
      kind: 'test', text: 'Would execute publish_roster on loadedhub (simulated)',
    });
  });

  it('strips tags in any order and combination', () => {
    expect(parseThinkingStep(`[reasoning] [ts:${TS}] Thinking.`)).toEqual({ time: TS, kind: 'reasoning', text: 'Thinking.' });
    expect(parseThinkingStep(`[test][ts:${TS}]Simulated.`)).toEqual({ time: TS, kind: 'test', text: 'Simulated.' });
    // Reasoning decides how the step is drawn when both are present.
    expect(parseThinkingStep('[test] [reasoning] Both.')).toEqual({ kind: 'reasoning', text: 'Both.' });
  });

  it('keeps multi-line reasoning intact', () => {
    expect(parseThinkingStep(`[ts:${TS}] [reasoning] Line one.\n\nLine two.`).text).toBe('Line one.\n\nLine two.');
  });

  it('only strips leading tags it knows', () => {
    expect(parseThinkingStep('[note] keep me').text).toBe('[note] keep me');
    expect(parseThinkingStep('Checked [test] data').text).toBe('Checked [test] data');
    expect(parseThinkingStep(`Saw [ts:${TS}] mid-sentence`)).toEqual({ kind: 'step', text: `Saw [ts:${TS}] mid-sentence` });
  });

  it('treats an empty time as no time', () => {
    expect(parseThinkingStep('[ts:] Continuing…')).toEqual({ kind: 'step', text: 'Continuing…' });
  });
});
