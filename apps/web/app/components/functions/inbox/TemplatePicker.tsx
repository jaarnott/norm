'use client';

/**
 * "Insert a template" — the venue's reply templates (and the all-venue ones)
 * for the booking type, the stage that fits the conversation first.
 */
import { useEffect, useState } from 'react';
import { inboxApi } from './inboxApi';
import type { ReplyTemplate, TemplateStage } from './types';

const STAGE_LABEL: Record<TemplateStage, string> = { first_reply: 'First reply', follow_up: 'Follow-up' };

export default function TemplatePicker({
  venueId,
  bookingType,
  stage,
  onPick,
  disabled = false,
}: {
  venueId: string | null;
  bookingType?: string | null;
  /** The stage that fits (first reply until the venue has written). */
  stage: TemplateStage;
  onPick: (template: ReplyTemplate) => void;
  disabled?: boolean;
}) {
  const [loaded, setLoaded] = useState<{ key: string; rows: ReplyTemplate[]; error: boolean } | null>(null);
  const key = `${venueId}|${bookingType}`;

  useEffect(() => {
    let alive = true;
    inboxApi
      .replyTemplates({ venue_id: venueId, booking_type: bookingType })
      .then((r) => alive && setLoaded({ key, rows: r.rows, error: false }))
      .catch(() => alive && setLoaded({ key, rows: [], error: true }));
    return () => {
      alive = false;
    };
  }, [key, venueId, bookingType]);

  const rows = loaded?.key === key ? loaded.rows : null;
  const order: TemplateStage[] = stage === 'first_reply' ? ['first_reply', 'follow_up'] : ['follow_up', 'first_reply'];
  const placeholder =
    rows === null ? 'Loading templates…' : loaded?.error ? 'Templates unavailable' : rows.length ? 'Insert a template…' : 'No templates';

  return (
    <select
      className="n-select"
      aria-label="Insert a reply template"
      value=""
      disabled={disabled || !rows?.length}
      onChange={(e) => {
        const picked = rows?.find((t) => t.id === e.target.value);
        if (picked) onPick(picked);
      }}
      style={{ maxWidth: 240, height: 30, fontSize: 'var(--fs-sm)' }}
    >
      <option value="">{placeholder}</option>
      {order.map((s) => {
        const group = (rows || []).filter((t) => t.stage === s);
        if (!group.length) return null;
        return (
          <optgroup key={s} label={s === stage ? `${STAGE_LABEL[s]} (suggested)` : STAGE_LABEL[s]}>
            {group.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </optgroup>
        );
      })}
    </select>
  );
}
