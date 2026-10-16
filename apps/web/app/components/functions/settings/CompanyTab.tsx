'use client';

/**
 * Settings → Company: a pointer, not a form. Currency, the default time
 * zone, tax wording and the Functions mode are organisation settings and
 * live in Norm's own Settings → Organisation → Company (CompanySettingsTab);
 * this shows what they are and links there.
 */
import type { ReactNode } from 'react';
import Badge from '../../ui/Badge';
import type { FnMeta } from '../shared/meta';
import { NormSettingsButton, Panel, Section, help } from './ui';

const MODE: Record<string, { tone: 'ok' | 'warn' | 'neutral'; label: string; detail: string }> = {
  live: { tone: 'ok', label: 'Live', detail: 'Norm is the system of record for functions.' },
  shadow: { tone: 'warn', label: 'Shadow', detail: 'Orbit is still live; Norm holds a read-only copy refreshed from it.' },
  off: { tone: 'neutral', label: 'Off', detail: 'Functions is switched off.' },
};

export default function CompanyTab({ meta }: { meta: FnMeta }) {
  const c = meta.company;
  const mode = MODE[meta.mode] ?? MODE.off;
  const rows: [string, ReactNode][] = [
    ['Currency', c.currency],
    ['Default time zone', c.default_timezone.replace(/_/g, ' ')],
    ['Tax wording', `“${c.tax_label}”`],
    ['Functions mode', <span key="m" style={{ display: 'inline-flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}><Badge tone={mode.tone}>{mode.label}</Badge><span style={{ color: 'var(--text-soft)' }}>{mode.detail}</span></span>],
  ];
  return (
    <Section title="Company settings" meta="Set once for the whole organisation, in Norm’s Settings">
      <Panel style={{ maxWidth: 720 }}>
        <dl style={{ display: 'grid', gridTemplateColumns: 'minmax(120px, 180px) minmax(0, 1fr)', gap: '10px 16px', margin: 0, fontSize: 'var(--fs-base)' }}>
          {rows.map(([k, v]) => (
            <div key={k} style={{ display: 'contents' }}>
              <dt style={{ color: 'var(--muted)' }}>{k}</dt>
              <dd style={{ margin: 0, minWidth: 0 }}>{v}</dd>
            </div>
          ))}
        </dl>
        <p style={{ ...help, marginTop: 14 }}>
          Money in Functions is shown in this currency with this tax wording. Each venue keeps its own time zone (Norm’s Settings →
          Venues); the default is for dates that belong to no venue. Owners and admins change these, and switch Functions between off,
          shadow and live.
        </p>
        <div style={{ marginTop: 14 }}>
          <NormSettingsButton tab="company" label="Open company settings" />
        </div>
      </Panel>
    </Section>
  );
}
