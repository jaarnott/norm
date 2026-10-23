import { describe, it, expect } from 'vitest';
import {
  MARKER,
  TOOL_FILTER,
  applySettings,
  buildPrompt,
  createBody,
  fromTimeValue,
  isWeeklyReportTask,
  parseSettings,
  readSchedule,
  scheduleLabel,
  splitRecipients,
  updateBody,
} from './schedule';

describe('the prompt template', () => {
  it('round-trips recipients and venues', () => {
    const prompt = buildPrompt({ recipients: ['anna@venue.co.nz', 'ben@venue.co.nz'], venues: 'all' });
    expect(prompt.startsWith(MARKER)).toBe(true);
    expect(parseSettings(prompt)).toEqual({ recipients: ['anna@venue.co.nz', 'ben@venue.co.nz'], venues: 'all', standard: true });
    expect(prompt).toContain('get_daily_performance with venues "all"');
    expect(prompt).toContain('content_markdown');
  });

  it('names one venue in the steps when the report is for one venue', () => {
    const prompt = buildPrompt({ recipients: ['a@b.co'], venues: 'La Zeppa' });
    expect(parseSettings(prompt).venues).toBe('La Zeppa');
    expect(prompt).toContain('venues ["La Zeppa"]');
    expect(prompt).not.toContain('Group row');
  });

  it('changes only the Send to line, keeping instructions added in chat', () => {
    const base = buildPrompt({ recipients: ['a@b.co'], venues: 'all' });
    const edited = `${base}\n7. Also mention the weather.`;
    const next = applySettings(edited, { recipients: ['c@d.co', 'e@f.co'], venues: 'all' });
    expect(next).toContain('Send to: c@d.co, e@f.co');
    expect(next).not.toContain('a@b.co');
    expect(next).toContain('7. Also mention the weather.');
  });

  it('rebuilds the steps when the venue changes', () => {
    const base = buildPrompt({ recipients: ['a@b.co'], venues: 'all' });
    const next = applySettings(base, { recipients: ['a@b.co'], venues: 'Mr Murdochs' });
    expect(next).toContain('venues ["Mr Murdochs"]');
  });

  it('puts the standard template back on a prompt without its lines', () => {
    const chat = 'Every Monday email felipe@cbhg.co.nz the weekly sales report.';
    expect(parseSettings(chat)).toEqual({ recipients: ['felipe@cbhg.co.nz'], venues: 'all', standard: false });
    const next = applySettings(chat, { recipients: ['felipe@cbhg.co.nz'], venues: 'all' });
    expect(next.startsWith(MARKER)).toBe(true);
  });
});

describe('finding the report tasks', () => {
  it('recognises the marker or the exact title, nothing looser', () => {
    expect(isWeeklyReportTask({ title: 'Anything', prompt: buildPrompt({ recipients: [], venues: 'all' }) })).toBe(true);
    expect(isWeeklyReportTask({ title: ' weekly report ', prompt: 'x' })).toBe(true);
    expect(isWeeklyReportTask({ title: 'Kitchen Labour Report — Weekly', prompt: 'weekly report of wages' })).toBe(false);
  });
});

describe('recipients', () => {
  it('splits on commas, spaces and new lines, deduplicating', () => {
    expect(splitRecipients('a@b.co, A@b.co\n<c@d.co.nz>; nope')).toEqual({ valid: ['a@b.co', 'c@d.co.nz'], invalid: ['nope'] });
  });
});

describe('the schedule', () => {
  it('reads a config back with the scheduler defaults', () => {
    expect(readSchedule({ day_of_week: 'tue', hour: 7, minute: 30 })).toEqual({ day: 'tuesday', hour: 7, minute: 30 });
    expect(readSchedule({})).toEqual({ day: 'monday', hour: 9, minute: 0 });
    expect(scheduleLabel({ day: 'monday', hour: 8, minute: 0 })).toBe('Mondays at 8:00am');
    expect(scheduleLabel({ day: 'sunday', hour: 12, minute: 15 })).toBe('Sundays at 12:15pm');
    expect(fromTimeValue('25:00')).toBeNull();
    expect(fromTimeValue('08:45')).toEqual({ hour: 8, minute: 45 });
  });

  it('builds the create and update requests the API takes', () => {
    const create = createBody({ recipients: ['a@b.co'], venues: 'all' }, { day: 'monday', hour: 8, minute: 0 }, 'Pacific/Auckland');
    expect(create).toMatchObject({
      title: 'Weekly Report',
      agent_slug: 'reports',
      schedule_type: 'weekly',
      schedule_config: { day_of_week: 'monday', hour: 8, minute: 0, timezone: 'Pacific/Auckland' },
      tool_filter: TOOL_FILTER,
    });
    const task = { prompt: create.prompt, schedule_config: create.schedule_config, tool_filter: ['get_sales', 'send_report_email'] };
    const update = updateBody(task, { recipients: ['x@y.co'], venues: 'all' }, { day: 'friday', hour: 16, minute: 30 });
    expect(update.schedule_config).toEqual({ day_of_week: 'friday', hour: 16, minute: 30, timezone: 'Pacific/Auckland' });
    expect(update.prompt).toContain('Send to: x@y.co');
    expect(update.tool_filter).toEqual(['get_sales', 'send_report_email', 'get_daily_performance', 'get_cogs_detail_for_period', 'search_tool_result']);
    expect(updateBody({ ...task, tool_filter: null }, { recipients: ['x@y.co'], venues: 'all' }, { day: 'friday', hour: 16, minute: 30 }).tool_filter).toBeUndefined();
  });
});
