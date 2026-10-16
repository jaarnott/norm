'use client';

/**
 * A web form's theme: named tokens (colours, fonts, sizes, corner radii) the
 * embedded form applies, with a preview of the form in them. Values are
 * plain — no markup or CSS punctuation (the API refuses `;{}<>`).
 */
import { useId, useState } from 'react';
import { Plus, Trash2 } from 'lucide-react';
import Button from '../../ui/Button';
import IconButton from '../../ui/IconButton';
import { useBreakpoint } from '../../../hooks/useBreakpoint';
import { fnSend } from '../shared/api';
import { errorText } from './errors';
import { SUGGESTED_TOKENS, looksLikeColour, sixDigitHex, themeStyles, tokenLabel, type Theme } from './formTheme';
import type { FormField, WebForm } from './types';
import { Notice, SaveRow, help, mono, muted } from './ui';

interface Entry {
  key: string;
  value: string;
}

const entriesOf = (theme: WebForm['theme']): Entry[] => Object.entries(theme ?? {}).map(([key, value]) => ({ key, value: String(value) }));
const themeOf = (entries: Entry[]): Theme => Object.fromEntries(entries.filter((e) => e.key.trim()).map((e) => [e.key.trim(), e.value.trim()]));

function problems(entries: Entry[]): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  for (const e of entries) {
    const k = e.key.trim();
    if (!k) continue;
    if (!/^[A-Za-z][A-Za-z0-9_]{0,29}$/.test(k)) out.push(`“${k}” isn’t a usable name — letters, numbers and _ only.`);
    if (seen.has(k)) out.push(`“${k}” is listed twice.`);
    seen.add(k);
    if (/[<>{};\\]/.test(e.value) || /url\(/i.test(e.value)) out.push(`${tokenLabel(k)}: values can’t contain < > { } ; \\ or url(…).`);
    if (e.value.length > 100) out.push(`${tokenLabel(k)}: keep values under 100 characters.`);
  }
  return out;
}

export default function FormThemeEditor({ form, canWrite, onSaved }: {
  form: WebForm;
  canWrite: boolean;
  onSaved: (f: WebForm) => void;
}) {
  const id = useId();
  const [entries, setEntries] = useState<Entry[]>(() => entriesOf(form.theme));
  const [touched, setTouched] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { isDesktop } = useBreakpoint();

  const edit = (next: Entry[]) => {
    setEntries(next);
    setTouched(true);
    setSaved(false);
    setError(null);
  };
  const set = (i: number, p: Partial<Entry>) => edit(entries.map((e, j) => (j === i ? { ...e, ...p } : e)));
  const issues = problems(entries);
  const theme = themeOf(entries);
  const missing = SUGGESTED_TOKENS.filter((s) => !(s.key in theme));

  const save = async () => {
    if (issues.length) {
      setError(issues[0]);
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const next = await fnSend<WebForm>('PUT', `/forms/${form.id}`, { expected_version: form.version, theme });
      setEntries(entriesOf(next.theme));
      setTouched(false);
      setSaved(true);
      onSaved(next);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div style={{ display: 'grid', gridTemplateColumns: isDesktop ? 'minmax(0, 1fr) minmax(0, 1fr)' : 'minmax(0, 1fr)', gap: 20, alignItems: 'start' }}>
      <div className="n-card" style={{ padding: 16, minWidth: 0 }}>
        {entries.length === 0 && <p style={{ ...muted, marginTop: 0 }}>No theme yet — the form uses plain defaults.</p>}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {entries.map((e, i) => {
            const colour = looksLikeColour(e.value);
            return (
              <div key={i} style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1fr) auto', gap: 6, alignItems: 'center' }}>
                <input className="n-input" aria-label={`Setting ${i + 1} name`} value={e.key} disabled={!canWrite} title={tokenLabel(e.key)}
                  onChange={(ev) => set(i, { key: ev.target.value })} style={{ minWidth: 0, ...mono }} placeholder="name" />
                <span style={{ display: 'flex', gap: 4, minWidth: 0 }}>
                  {colour && (
                    <input type="color" aria-label={`${tokenLabel(e.key)} colour`} value={sixDigitHex(e.value)} disabled={!canWrite}
                      onChange={(ev) => set(i, { value: ev.target.value })}
                      style={{ width: 36, height: 34, padding: 2, flex: '0 0 auto', border: '1px solid var(--line-strong)', borderRadius: 'var(--radius-sm)', background: 'var(--bg)' }} />
                  )}
                  <input className="n-input" aria-label={`${tokenLabel(e.key) || `Setting ${i + 1}`} value`} value={e.value} disabled={!canWrite}
                    onChange={(ev) => set(i, { value: ev.target.value })} style={{ flex: 1, minWidth: 0, ...mono }} placeholder="value" />
                </span>
                {canWrite ? (
                  <IconButton icon={Trash2} iconSize={16} label={`Remove ${e.key || 'setting'}`} onClick={() => edit(entries.filter((_, j) => j !== i))} />
                ) : <span />}
              </div>
            );
          })}
        </div>
        {canWrite && (
          <div style={{ marginTop: 10, display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
            <Button size="sm" variant="quiet" icon={Plus} onClick={() => edit([...entries, { key: '', value: '' }])}>Add setting</Button>
            {missing.length > 0 && (
              <select className="n-select" aria-label="Add a common setting" value="" style={{ height: 30, fontSize: 'var(--fs-sm)' }}
                onChange={(ev) => {
                  const s = SUGGESTED_TOKENS.find((x) => x.key === ev.target.value);
                  if (s) edit([...entries, { key: s.key, value: s.sample }]);
                }}>
                <option value="">Add a common setting…</option>
                {missing.map((s) => <option key={s.key} value={s.key}>{tokenLabel(s.key)}</option>)}
              </select>
            )}
          </div>
        )}
        <p style={help}>Sizes in pixels can be plain numbers (16); 999 makes a pill-shaped corner. Fonts must already be on the website.</p>
        {touched && issues.length > 0 && <Notice tone="warn" style={{ marginTop: 10 }}>{issues.map((p) => <div key={p}>{p}</div>)}</Notice>}
        <SaveRow canWrite={canWrite} dirty={touched} saving={saving} saved={saved} error={error} onSave={save}
          onDiscard={() => { setEntries(entriesOf(form.theme)); setTouched(false); setError(null); }} label="Save theme" />
      </div>
      <div style={{ minWidth: 0 }}>
        <div id={`${id}-preview`} className="n-eyebrow" style={{ marginBottom: 6 }}>Preview</div>
        <ThemePreview form={form} theme={theme} labelledBy={`${id}-preview`} />
      </div>
    </div>
  );
}

/** The form's own fields drawn in the theme (inline styles = element.style). */
function ThemePreview({ form, theme, labelledBy }: { form: WebForm; theme: Theme; labelledBy: string }) {
  const s = themeStyles(theme);
  const fields: FormField[] = (form.fields ?? []).slice(0, 8);
  return (
    <div role="img" aria-labelledby={labelledBy} className="n-card" style={{ overflow: 'hidden' }}>
      <div style={{ ...s.form, maxWidth: undefined }} inert>
        <div style={s.heading}>{form.name}</div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: s.gap }}>
          {fields.map((f) => {
            if (f.type === 'section') return <div key={f.key} style={s.section}>{f.label}</div>;
            const label = <span style={s.label}>{f.label}{f.required ? ' *' : ''}</span>;
            if (f.type === 'long_text') return <div key={f.key}>{label}<textarea readOnly tabIndex={-1} style={s.textarea} placeholder={f.placeholder ?? ''} /></div>;
            if (f.type === 'checkbox' || f.type === 'choice') {
              const opts = Array.isArray(f.options) ? f.options.slice(0, 3) : [];
              return (
                <div key={f.key}>
                  {label}
                  <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
                    {(opts.length ? opts : [{ value: 'yes', label: 'Yes' }]).map((o) => (
                      <span key={o.value} style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                        <span style={{ width: 16, height: 16, border: `1px solid ${String(s.input.borderColor)}`, borderRadius: f.type === 'choice' ? 999 : 4, background: String(s.input.background) }} />
                        {o.label}
                      </span>
                    ))}
                  </div>
                </div>
              );
            }
            if (f.type === 'name') {
              return (
                <div key={f.key}>
                  {label}
                  <div style={{ display: 'flex', gap: 8 }}>
                    <input readOnly tabIndex={-1} style={s.input} placeholder="First" />
                    <input readOnly tabIndex={-1} style={s.input} placeholder="Last" />
                  </div>
                </div>
              );
            }
            return <div key={f.key}>{label}<input readOnly tabIndex={-1} style={s.input} placeholder={f.placeholder ?? ''} /></div>;
          })}
        </div>
        <div style={{ marginTop: 18 }}>
          <span style={{ ...s.button, display: 'inline-block' }}>{form.submit_label || 'Send enquiry'}</span>
        </div>
      </div>
    </div>
  );
}
