'use client';

/**
 * Set up, or change, one weekly report email: who gets it, which day and
 * time, and which venues. Saving writes the automated task through the
 * existing task API (schedule.ts builds the requests).
 */

import React, { useMemo, useState } from 'react';
import { apiFetch } from '../../../lib/api';
import type { AutomatedTask } from '../../../types';
import { Field, FormDialog } from '../../functions/enquiries/bits';
import {
  DAYS,
  createBody,
  dayName,
  fromTimeValue,
  parseSettings,
  readSchedule,
  splitRecipients,
  timeValue,
  updateBody,
  type Day,
} from './schedule';
import { clockLabel } from './report';

async function detail(res: Response, fallback: string): Promise<string> {
  const body = await res.json().catch(() => null);
  const d = body?.detail ?? body?.error;
  return typeof d === 'string' && d ? d : `${fallback} (error ${res.status})`;
}

export default function ScheduleDialog({ task, venueNames, defaultRecipient, timezone, dayStart, onSaved, onClose }: {
  /** The task to change; null sets a new one up. */
  task: AutomatedTask | null;
  venueNames: string[];
  defaultRecipient: string | null;
  /** The venues' timezone (from the tools' window), for a new task's schedule. */
  timezone: string | null;
  /** When the venues' trading day starts ("07:00"): last week is complete after it on Monday. */
  dayStart: string | null;
  onSaved: (notice?: string) => void;
  onClose: () => void;
}) {
  const current = useMemo(() => (task ? parseSettings(task.prompt) : null), [task]);
  const when = useMemo(() => (task ? readSchedule(task.schedule_config) : { day: 'monday' as Day, hour: 8, minute: 0 }), [task]);

  const [to, setTo] = useState<string>(current ? current.recipients.join(', ') : (defaultRecipient ?? ''));
  const [day, setDay] = useState<Day>(when.day);
  const [time, setTime] = useState<string>(timeValue(when));
  const [venues, setVenues] = useState<string>(current?.venues ?? 'all');

  const parsed = splitRecipients(to);
  const clock = fromTimeValue(time);
  const invalid = parsed.invalid.length > 0;
  const empty = parsed.valid.length === 0;
  // A venue the prompt names that the organisation no longer has stays
  // choosable, so opening the dialog never silently changes it.
  const venueOptions = venues !== 'all' && !venueNames.includes(venues) ? [...venueNames, venues] : venueNames;

  const save = async () => {
    if (!clock) throw new Error('Choose a time.');
    const settings = { recipients: parsed.valid, venues };
    const schedule = { day, hour: clock.hour, minute: clock.minute };
    if (task) {
      const res = await apiFetch(`/api/automated-tasks/${task.id}`, {
        method: 'PUT',
        body: JSON.stringify(updateBody(task, settings, schedule)),
      });
      if (!res.ok) throw new Error(await detail(res, 'Could not save the changes'));
      onSaved();
      return;
    }
    const res = await apiFetch('/api/automated-tasks', {
      method: 'POST',
      body: JSON.stringify(createBody(settings, schedule, timezone)),
    });
    if (!res.ok) throw new Error(await detail(res, 'Could not set the report up'));
    const created = (await res.json().catch(() => null)) as AutomatedTask | null;
    // New tasks are drafts; switch it on. If that fails the task still
    // exists, so close and say so rather than invite a second copy.
    if (created?.id) {
      const on = await apiFetch(`/api/automated-tasks/${created.id}/resume`, { method: 'POST' });
      if (!on.ok) {
        onSaved(`The report is set up but isn't switched on yet: ${await detail(on, 'it could not be switched on')}`);
        return;
      }
    }
    onSaved();
  };

  const startsAt = clockLabel(dayStart);

  return (
    <FormDialog
      title={task ? 'Change the weekly email' : 'Set up the weekly email'}
      submitLabel={task ? 'Save' : 'Start sending'}
      busyLabel="Saving…"
      submitDisabled={empty || invalid || !clock}
      onSubmit={save}
      onClose={onClose}
    >
      <Field
        label="Send to"
        hint={invalid ? (
          <span style={{ color: 'var(--error)' }}>Not an email address: {parsed.invalid.join(', ')}</span>
        ) : 'Email addresses, separated by commas.'}
      >
        {(id) => (
          <textarea
            id={id}
            className="n-input"
            rows={2}
            value={to}
            onChange={(e) => setTo(e.target.value)}
            placeholder="manager@yourvenue.co.nz, owner@yourvenue.co.nz"
            style={{ width: '100%' }}
          />
        )}
      </Field>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 12 }}>
        <Field label="Day">
          {(id) => (
            <select id={id} className="n-select" value={day} onChange={(e) => setDay(e.target.value as Day)} style={{ width: '100%' }}>
              {DAYS.map((d) => <option key={d} value={d}>{dayName(d)}</option>)}
            </select>
          )}
        </Field>
        <Field label="Time">
          {(id) => (
            <input id={id} type="time" className="n-input" step={900} value={time} onChange={(e) => setTime(e.target.value)} style={{ width: '100%' }} />
          )}
        </Field>
      </div>
      {day === 'monday' && clock && startsAt && timeValue(clock) < (dayStart || '00:00').slice(0, 5) && (
        <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--warn)' }}>
          Sunday&rsquo;s trading runs until {startsAt} on Monday. Send after that so the report has the whole week.
        </div>
      )}
      <Field label="Venues">
        {(id) => (
          <select id={id} className="n-select" value={venues} onChange={(e) => setVenues(e.target.value)} style={{ width: '100%' }}>
            <option value="all">All venues</option>
            {venueOptions.map((v) => <option key={v} value={v}>{v}</option>)}
          </select>
        )}
      </Field>
      {task && current && !current.standard && (
        <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)', background: 'var(--surface-alt)', borderRadius: 'var(--radius)', padding: '8px 12px' }}>
          This report&rsquo;s instructions were changed in its conversation. Saving here puts back the standard weekly report, sent to the people above.
        </div>
      )}
    </FormDialog>
  );
}
