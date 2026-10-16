'use client';

/**
 * Settings → Brand: the venue's proposal identity — logo, colours, fonts,
 * what's included, terms, sign-off, how long a quote is valid — and the
 * brand template (free HTML + CSS) every proposal starts from, with a live
 * sample preview. Saved templates are sanitised by the API (no scripts,
 * remote images or web fonts); the editor then shows what came back.
 */
import { useEffect, useId, useState } from 'react';
import { Plus, RotateCcw, Trash2, Wand2 } from 'lucide-react';
import Button from '../../ui/Button';
import IconButton from '../../ui/IconButton';
import PageState from '../../ui/PageState';
import { ConfirmDialog } from '../../ui/Dialog';
import { useBreakpoint } from '../../../hooks/useBreakpoint';
import { fnSend } from '../shared/api';
import { FONT_STACKS } from '../shared/fonts';
import type { FnMeta, FnVenue } from '../shared/meta';
import { buildPreview, defaultCss, defaultTemplateHtml, TEMPLATE_TOKENS } from './brandPreview';
import { ACCESSIBLE_DEFAULTS, SERVER_DEFAULTS, contrastIssues } from './contrast';
import { errorText } from './errors';
import { sixDigitHex } from './formTheme';
import ImageLibrary from './ImageLibrary';
import { moveRow } from './menuTemplate';
import { uploadImage } from './upload';
import { useFileUrl } from './useFileUrl';
import type { Brand, BrandColourKey, BrandTerm, FnFile, LibraryImage, VenueProfile } from './types';
import {
  AuthImage, Field, FieldGrid, MoveButtons, Notice, Panel, ReadOnlyNote, Section, UploadButton, UploadList,
  fullWidth, help, mono, muted, useUploads,
} from './ui';

interface Draft {
  logo_file_id: string | null;
  cover_file_id: string | null;
  closing_file_id: string | null;
  wordmark: string;
  tagline: string;
  footer_contact: string;
  valid_days: string;
  colours: Record<BrandColourKey, string>;
  fonts: { heading: string; body: string };
  inclusions: string[];
  terms: { title: string; body: string }[];
  signoff: { title: string; body: string; contact: string };
  template_html: string;
  template_css: string;
}

const COLOURS: { key: BrandColourKey; label: string; hint: string }[] = [
  { key: 'primary', label: 'Primary', hint: 'Headings and the wordmark' },
  { key: 'accent', label: 'Accent', hint: 'Rules, small labels, dietary tags' },
  { key: 'panel', label: 'Panel', hint: 'Menu and space boxes' },
  { key: 'text', label: 'Text', hint: 'Body text' },
  { key: 'background', label: 'Background', hint: 'The page' },
];

function termOf(t: BrandTerm): { title: string; body: string } {
  return typeof t === 'string' ? { title: '', body: t } : { title: t.title ?? '', body: t.body ?? '' };
}

function draftOf(b: Brand | null): Draft {
  return {
    logo_file_id: b?.logo_file_id ?? null,
    cover_file_id: b?.cover_file_id ?? null,
    closing_file_id: b?.closing_file_id ?? null,
    wordmark: b?.wordmark ?? '',
    tagline: b?.tagline ?? '',
    footer_contact: b?.footer_contact ?? '',
    valid_days: String(b?.valid_days ?? 14),
    colours: {
      primary: b?.colours?.primary ?? '',
      accent: b?.colours?.accent ?? '',
      panel: b?.colours?.panel ?? '',
      text: b?.colours?.text ?? '',
      background: b?.colours?.background ?? '',
    },
    fonts: { heading: b?.fonts?.heading ?? '', body: b?.fonts?.body ?? '' },
    inclusions: [...(b?.inclusions ?? [])],
    terms: (b?.terms ?? []).map(termOf),
    signoff: { title: b?.signoff?.title ?? '', body: b?.signoff?.body ?? '', contact: b?.signoff?.contact ?? '' },
    template_html: b?.template_html ?? '',
    template_css: b?.template_css ?? '',
  };
}

const compact = <T extends Record<string, string>>(o: T) =>
  Object.fromEntries(Object.entries(o).map(([k, v]) => [k, v.trim()]).filter(([, v]) => v));

/** The brand as the API stores it (blank → null / left out). */
function payload(d: Draft) {
  return {
    logo_file_id: d.logo_file_id,
    cover_file_id: d.cover_file_id,
    closing_file_id: d.closing_file_id,
    wordmark: d.wordmark.trim() || null,
    tagline: d.tagline.trim() || null,
    footer_contact: d.footer_contact.trim() || null,
    valid_days: Number(d.valid_days),
    colours: compact(d.colours),
    fonts: compact(d.fonts),
    inclusions: d.inclusions.map((s) => s.trim()).filter(Boolean),
    // A term without a title stays a plain line (how the import stored them).
    terms: d.terms
      .filter((t) => t.title.trim() || t.body.trim())
      .map((t) => (t.title.trim() ? { title: t.title.trim(), body: t.body.trim() } : t.body.trim())),
    signoff: compact(d.signoff),
    template_html: d.template_html.trim() ? d.template_html : null,
    template_css: d.template_css.trim() ? d.template_css : null,
  };
}

export default function BrandTab({ meta, venue, profile, loading, error, setProfile, onMetaChanged }: {
  meta: FnMeta;
  venue: FnVenue;
  profile: VenueProfile | null;
  loading: boolean;
  error: string | null;
  setProfile: (update: (p: VenueProfile) => VenueProfile) => void;
  onMetaChanged: () => void;
}) {
  if (error) return <PageState kind="error" title={`Couldn’t load ${venue.name}`} detail={error} />;
  if (loading || !profile) return <PageState kind="loading" title={`Loading ${venue.name}…`} />;
  return <BrandEditor key={venue.id} meta={meta} venue={venue} profile={profile} setProfile={setProfile} onMetaChanged={onMetaChanged} />;
}

function BrandEditor({ meta, venue, profile, setProfile, onMetaChanged }: {
  meta: FnMeta;
  venue: FnVenue;
  profile: VenueProfile;
  setProfile: (update: (p: VenueProfile) => VenueProfile) => void;
  onMetaChanged: () => void;
}) {
  const brand = profile.brand;
  const canWrite = meta.can_write;
  const [draft, setDraft] = useState<Draft>(() => draftOf(brand));
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [confirmReset, setConfirmReset] = useState(false);
  const [confirmLayout, setConfirmLayout] = useState(false);
  const { isDesktop } = useBreakpoint();
  const id = useId();

  const current = payload(draft);
  const base = payload(draftOf(brand));
  const changed = Object.fromEntries(
    Object.entries(current).filter(([k, v]) => JSON.stringify(v) !== JSON.stringify(base[k as keyof typeof base])),
  );
  const dirty = Object.keys(changed).length > 0;
  const days = Number(draft.valid_days);
  const daysBad = !Number.isInteger(days) || days < 1 || days > 365;
  const badColours = COLOURS.filter((c) => draft.colours[c.key].trim() && !/^#([0-9a-f]{3}|[0-9a-f]{6})$/i.test(draft.colours[c.key].trim()));
  const issues = contrastIssues(current.colours);

  const edit = (p: Partial<Draft>) => {
    setDraft((d) => ({ ...d, ...p }));
    setSaved(false);
    setSaveError(null);
  };

  const save = async () => {
    if (daysBad) {
      setSaveError('A quote is valid for 1 to 365 days.');
      return;
    }
    if (badColours.length) {
      setSaveError(`${badColours.map((c) => c.label).join(', ')}: colours are hex values like #1f2a44.`);
      return;
    }
    setSaving(true);
    setSaveError(null);
    try {
      const next = await fnSend<Brand>('PUT', `/venues/${venue.id}/brand`, { ...(brand ? { version: brand.version } : {}), ...changed });
      setProfile((p) => ({ ...p, brand: next }));
      setDraft(draftOf(next));
      setSaved(true);
      if (!brand) onMetaChanged(); // has_brand
    } catch (e) {
      setSaveError(errorText(e));
    } finally {
      setSaving(false);
    }
  };

  const reset = async () => {
    try {
      await fnSend('DELETE', `/venues/${venue.id}/brand`);
    } catch (e) {
      throw new Error(errorText(e));
    }
    setProfile((p) => ({ ...p, brand: null }));
    setDraft(draftOf(null));
    setSaved(false);
    onMetaChanged();
  };

  // ----- live preview (debounced so typing doesn't reload the frame each key)
  const logo = useFileUrl(draft.logo_file_id);
  const origin = typeof window === 'undefined' ? '' : window.location.origin;
  const bookingLabel = meta.booking_types.find((b) => b.value === 'large_event_exclusive')?.label ?? 'Large event';
  const doc = buildPreview({
    venueName: venue.name,
    wordmark: draft.wordmark,
    tagline: draft.tagline,
    logoUrl: logo.url,
    footer: draft.footer_contact,
    validDays: daysBad ? 14 : days,
    colours: current.colours,
    fonts: current.fonts,
    inclusions: draft.inclusions,
    terms: draft.terms,
    signoff: draft.signoff,
    templateHtml: draft.template_html,
    templateCss: draft.template_css,
    currency: meta.company.currency,
    taxLabel: meta.company.tax_label,
    bookingLabel,
    areas: profile.areas.filter((a) => a.active),
  }, origin);
  const [shown, setShown] = useState(doc);
  useEffect(() => {
    const t = setTimeout(() => setShown(doc), 350);
    return () => clearTimeout(t);
  }, [doc]);

  const setImages = (update: (list: LibraryImage[]) => LibraryImage[]) => setProfile((p) => ({ ...p, images: update(p.images) }));

  const startFromLayout = () => {
    edit({ template_html: defaultTemplateHtml(draft.tagline), template_css: defaultCss(current.colours, current.fonts) });
  };

  const actions = canWrite ? (
    <>
      <Button size="sm" variant="danger" icon={RotateCcw} onClick={() => setConfirmReset(true)} disabled={!brand || saving}>
        Reset to default
      </Button>
      <Button variant="primary" onClick={save} disabled={!dirty || saving} aria-busy={saving || undefined}>
        {saving ? 'Saving…' : 'Save brand'}
      </Button>
    </>
  ) : <ReadOnlyNote />;

  const form = (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16, minWidth: 0 }}>
      <Panel>
        <span className="n-eyebrow">Identity</span>
        <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 14 }}>
          <BrandImageField label="Logo" fileId={draft.logo_file_id} images={profile.images} canWrite={canWrite}
            onChange={(fid) => edit({ logo_file_id: fid })} upload
            hint="Shown at the top of every proposal. Without one, the wordmark is used." />
          <FieldGrid min={200}>
            <Field label="Wordmark" htmlFor={`${id}-wm`}>
              <input id={`${id}-wm`} className="n-input" style={fullWidth} value={draft.wordmark} placeholder={venue.name}
                disabled={!canWrite} onChange={(e) => edit({ wordmark: e.target.value })} />
            </Field>
            <Field label="Tagline" htmlFor={`${id}-tag`}>
              <input id={`${id}-tag`} className="n-input" style={fullWidth} value={draft.tagline} placeholder="Rooftop bar & restaurant"
                disabled={!canWrite} onChange={(e) => edit({ tagline: e.target.value })} />
            </Field>
          </FieldGrid>
          <FieldGrid min={200}>
            <Field label="Footer line" htmlFor={`${id}-foot`}>
              <input id={`${id}-foot`} className="n-input" style={fullWidth} value={draft.footer_contact} placeholder="Address | phone | email"
                disabled={!canWrite} onChange={(e) => edit({ footer_contact: e.target.value })} />
            </Field>
            <Field label="Quote valid for (days)" htmlFor={`${id}-days`} hint={daysBad ? 'Between 1 and 365.' : undefined}>
              <input id={`${id}-days`} className="n-input" style={fullWidth} inputMode="numeric" value={draft.valid_days}
                disabled={!canWrite} aria-invalid={daysBad || undefined}
                onChange={(e) => edit({ valid_days: e.target.value.replace(/[^0-9]/g, '') })} />
            </Field>
          </FieldGrid>
          <FieldGrid min={200}>
            <BrandImageField label="Cover image" fileId={draft.cover_file_id} images={profile.images} canWrite={canWrite}
              onChange={(fid) => edit({ cover_file_id: fid })} />
            <BrandImageField label="Closing image" fileId={draft.closing_file_id} images={profile.images} canWrite={canWrite}
              onChange={(fid) => edit({ closing_file_id: fid })} />
          </FieldGrid>
        </div>
      </Panel>

      <Panel>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
          <span className="n-eyebrow">Colours</span>
          {canWrite && (
            <Button size="sm" variant="quiet" onClick={() => edit({ colours: { ...ACCESSIBLE_DEFAULTS } })}>Use accessible defaults</Button>
          )}
        </div>
        <div style={{ marginTop: 10, display: 'grid', gap: 10 }}>
          {COLOURS.map((c) => {
            const value = draft.colours[c.key];
            const valid = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.test(value.trim());
            return (
              <div key={c.key} style={{ display: 'grid', gridTemplateColumns: 'minmax(96px, 140px) 44px minmax(0, 1fr)', gap: 8, alignItems: 'center' }}>
                <label htmlFor={`${id}-c-${c.key}`} style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
                  <span style={{ display: 'block', fontWeight: 500 }}>{c.label}</span>
                  <span style={{ fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>{c.hint}</span>
                </label>
                <input
                  type="color"
                  aria-label={`${c.label} colour picker`}
                  value={sixDigitHex(valid ? value.trim() : SERVER_DEFAULTS[c.key])}
                  disabled={!canWrite}
                  onChange={(e) => edit({ colours: { ...draft.colours, [c.key]: e.target.value } })}
                  style={{ width: 44, height: 34, padding: 2, border: '1px solid var(--line-strong)', borderRadius: 'var(--radius-sm)', background: 'var(--bg)' }}
                />
                <input
                  id={`${id}-c-${c.key}`}
                  className="n-input"
                  style={{ ...fullWidth, ...mono }}
                  value={value}
                  placeholder={`${SERVER_DEFAULTS[c.key]} (default)`}
                  disabled={!canWrite}
                  aria-invalid={(!!value.trim() && !valid) || undefined}
                  onChange={(e) => edit({ colours: { ...draft.colours, [c.key]: e.target.value } })}
                />
              </div>
            );
          })}
        </div>
        {badColours.length > 0 && (
          <Notice tone="error" style={{ marginTop: 10 }}>Colours are hex values like #1f2a44 ({badColours.map((c) => c.label).join(', ')}).</Notice>
        )}
        {issues.length > 0 && (
          <Notice tone="warn" style={{ marginTop: 10 }}>
            {issues.map((i) => <div key={`${i.fg}-${i.bg}`}>{i.message}</div>)}
          </Notice>
        )}
      </Panel>

      <Panel>
        <span className="n-eyebrow">Fonts</span>
        <datalist id={`${id}-fonts`}>{FONT_STACKS.map((f) => <option key={f} value={f} />)}</datalist>
        <div style={{ marginTop: 10 }}>
          <FieldGrid min={220}>
            <Field label="Headings" htmlFor={`${id}-fh`}>
              <input id={`${id}-fh`} className="n-input" style={fullWidth} list={`${id}-fonts`} value={draft.fonts.heading}
                placeholder="Georgia, serif (default)" disabled={!canWrite} onChange={(e) => edit({ fonts: { ...draft.fonts, heading: e.target.value } })} />
            </Field>
            <Field label="Body text" htmlFor={`${id}-fb`}>
              <input id={`${id}-fb`} className="n-input" style={fullWidth} list={`${id}-fonts`} value={draft.fonts.body}
                placeholder="system-ui, sans-serif (default)" disabled={!canWrite} onChange={(e) => edit({ fonts: { ...draft.fonts, body: e.target.value } })} />
            </Field>
          </FieldGrid>
        </div>
        <p style={help}>Norm hosts Playfair Display, Cormorant Garamond, Libre Baskerville, DM Serif Display, Inter, Montserrat, Poppins, Bebas Neue, JetBrains Mono and Courier Prime — pick one from the list. Any other font only shows on devices that have it, so add a common fallback — e.g. “Futura, Arial, sans-serif”.</p>
      </Panel>

      <Panel>
        <span className="n-eyebrow">What’s included</span>
        <LineList
          lines={draft.inclusions}
          canWrite={canWrite}
          what="inclusion"
          placeholder="A dedicated functions manager from planning to pack-down"
          onChange={(inclusions) => edit({ inclusions })}
        />
      </Panel>

      <Panel>
        <span className="n-eyebrow">Good to know (terms)</span>
        <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 10 }}>
          {draft.terms.length === 0 && <p style={{ ...muted, margin: 0 }}>No terms yet.</p>}
          {draft.terms.map((t, i) => (
            <div key={i} style={{ border: '1px solid var(--line)', borderRadius: 'var(--radius)', padding: 10, background: 'var(--surface)' }}>
              <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                <input className="n-input" aria-label={`Term ${i + 1} title`} style={{ flex: 1, minWidth: 0 }} value={t.title}
                  placeholder="Title (optional)" disabled={!canWrite}
                  onChange={(e) => edit({ terms: draft.terms.map((x, j) => (j === i ? { ...x, title: e.target.value } : x)) })} />
                {canWrite && (
                  <>
                    <MoveButtons index={i} count={draft.terms.length} what={`term ${i + 1}`} onMove={(d) => edit({ terms: moveRow(draft.terms, i, d) })} />
                    <IconButton icon={Trash2} iconSize={16} label={`Remove term ${i + 1}`} onClick={() => edit({ terms: draft.terms.filter((_, j) => j !== i) })} />
                  </>
                )}
              </div>
              <textarea className="n-input" aria-label={`Term ${i + 1}`} rows={3} style={{ ...fullWidth, marginTop: 6 }} value={t.body}
                disabled={!canWrite} onChange={(e) => edit({ terms: draft.terms.map((x, j) => (j === i ? { ...x, body: e.target.value } : x)) })} />
            </div>
          ))}
          {canWrite && (
            <Button size="sm" variant="quiet" icon={Plus} style={{ alignSelf: 'flex-start' }}
              onClick={() => edit({ terms: [...draft.terms, { title: '', body: '' }] })}>Add term</Button>
          )}
        </div>
      </Panel>

      <Panel>
        <span className="n-eyebrow">Sign-off</span>
        <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 12 }}>
          <Field label="Title" htmlFor={`${id}-st`}>
            <input id={`${id}-st`} className="n-input" style={fullWidth} value={draft.signoff.title} placeholder="Next steps"
              disabled={!canWrite} onChange={(e) => edit({ signoff: { ...draft.signoff, title: e.target.value } })} />
          </Field>
          <Field label="Message" htmlFor={`${id}-sb`}>
            <textarea id={`${id}-sb`} className="n-input" rows={3} style={fullWidth} value={draft.signoff.body}
              disabled={!canWrite} onChange={(e) => edit({ signoff: { ...draft.signoff, body: e.target.value } })} />
          </Field>
          <Field label="Contact" htmlFor={`${id}-sc`}>
            <textarea id={`${id}-sc`} className="n-input" rows={2} style={fullWidth} value={draft.signoff.contact} placeholder="Ash · Functions manager · hello@venue.co.nz"
              disabled={!canWrite} onChange={(e) => edit({ signoff: { ...draft.signoff, contact: e.target.value } })} />
          </Field>
        </div>
      </Panel>

      <Panel>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
          <span className="n-eyebrow">Proposal template</span>
          {canWrite && (
            <span style={{ display: 'inline-flex', gap: 6, flexWrap: 'wrap' }}>
              <Button size="sm" variant="quiet" icon={Wand2}
                onClick={() => (draft.template_html.trim() ? setConfirmLayout(true) : startFromLayout())}>
                Start from Norm’s layout
              </Button>
              {(draft.template_html || draft.template_css) && (
                <Button size="sm" variant="quiet" onClick={() => edit({ template_html: '', template_css: '' })}>Clear template</Button>
              )}
            </span>
          )}
        </div>
        <p style={help}>
          Optional HTML and CSS every new proposal starts from. Leave it empty to use Norm’s layout in the colours and fonts above.
          Scripts, forms, remote images and outside web fonts are removed when you save; the hosted fonts above work by name.
        </p>
        <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 12 }}>
          <Field label="HTML" htmlFor={`${id}-th`}>
            <textarea id={`${id}-th`} className="n-input" rows={12} spellCheck={false} style={{ ...fullWidth, ...mono, whiteSpace: 'pre', overflowWrap: 'normal' }}
              value={draft.template_html} disabled={!canWrite} placeholder="<article class=&quot;proposal&quot;>{{logo}} …</article>"
              onChange={(e) => edit({ template_html: e.target.value })} />
          </Field>
          <Field label="CSS" htmlFor={`${id}-tc`}>
            <textarea id={`${id}-tc`} className="n-input" rows={8} spellCheck={false} style={{ ...fullWidth, ...mono, whiteSpace: 'pre', overflowWrap: 'normal' }}
              value={draft.template_css} disabled={!canWrite} placeholder=".proposal h1 { color: #0e5340; }"
              onChange={(e) => edit({ template_css: e.target.value })} />
          </Field>
        </div>
        <details style={{ marginTop: 10 }}>
          <summary style={{ cursor: 'pointer', fontSize: 'var(--fs-sm)', color: 'var(--accent)' }}>Placeholders you can use</summary>
          <ul style={{ listStyle: 'none', margin: '8px 0 0', padding: 0, display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(min(240px, 100%), 1fr))', gap: '4px 16px' }}>
            {TEMPLATE_TOKENS.map((t) => (
              <li key={t.token} style={{ fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
                <code style={{ ...mono, color: 'var(--text)' }}>{`{{${t.token}}}`}</code> — {t.what}
              </li>
            ))}
          </ul>
        </details>
      </Panel>

      {(dirty || saveError || saved) && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          {canWrite && (
            <Button variant="secondary" onClick={save} disabled={!dirty || saving}>{saving ? 'Saving…' : 'Save brand'}</Button>
          )}
          {dirty && canWrite && <Button variant="quiet" onClick={() => { setDraft(draftOf(brand)); setSaveError(null); }}>Discard changes</Button>}
          {saved && !dirty && <span role="status" style={muted}>Saved</span>}
        </div>
      )}
      {saveError && <Notice tone="error">{saveError}</Notice>}
    </div>
  );

  const preview = (
    <div style={{ position: isDesktop ? 'sticky' : 'static', top: 12, minWidth: 0 }}>
      <div className="n-card" style={{ overflow: 'hidden' }}>
        <div style={{ padding: '8px 12px', borderBottom: '1px solid var(--line)', fontSize: 'var(--fs-xs)', color: 'var(--muted)' }}>
          {draft.template_html.trim() ? 'Your template' : 'Norm’s layout'} with sample details — how a new proposal starts
        </div>
        <iframe
          title="Proposal preview"
          sandbox="allow-same-origin allow-popups allow-popups-to-escape-sandbox"
          referrerPolicy="no-referrer"
          srcDoc={shown}
          style={{ display: 'block', width: '100%', height: isDesktop ? 'calc(100vh - 220px)' : 560, minHeight: 420, border: 0, background: 'var(--bg)' }}
        />
      </div>
    </div>
  );

  return (
    <div>
      <Section
        title="Proposal brand"
        meta={brand ? 'Every new proposal at this venue starts from this brand.' : 'Not set up yet — proposals use Norm’s defaults until you save.'}
        actions={actions}
      >
        {saveError && !dirty && <Notice tone="error" style={{ marginBottom: 12 }}>{saveError}</Notice>}
        <div style={{ display: 'grid', gridTemplateColumns: isDesktop ? 'minmax(0, 460px) minmax(0, 1fr)' : 'minmax(0, 1fr)', gap: 20, alignItems: 'start' }}>
          {form}
          {preview}
        </div>
      </Section>

      <ImageLibrary venueId={venue.id} images={profile.images} canWrite={canWrite} setImages={setImages} />

      {confirmReset && (
        <ConfirmDialog
          title={`Reset ${venue.name}’s brand?`}
          confirmLabel="Reset to default"
          busyLabel="Resetting…"
          danger
          onClose={() => setConfirmReset(false)}
          onConfirm={reset}
        >
          <p style={{ margin: 0 }}>
            The logo, colours, fonts, terms, sign-off and template go back to Norm’s defaults. Proposals already started keep
            their design, and the image library stays.
          </p>
        </ConfirmDialog>
      )}
      {confirmLayout && (
        <ConfirmDialog
          title="Replace the template?"
          confirmLabel="Replace with Norm’s layout"
          onClose={() => setConfirmLayout(false)}
          onConfirm={async () => startFromLayout()}
        >
          <p style={{ margin: 0 }}>Your current HTML and CSS are replaced (nothing is saved until you press Save brand).</p>
        </ConfirmDialog>
      )}
    </div>
  );
}

/** A list of one-line entries (inclusions) with add / reorder / remove. */
function LineList({ lines, canWrite, what, placeholder, onChange }: {
  lines: string[];
  canWrite: boolean;
  what: string;
  placeholder: string;
  onChange: (lines: string[]) => void;
}) {
  return (
    <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 6 }}>
      {lines.length === 0 && <p style={{ ...muted, margin: 0 }}>Nothing listed yet.</p>}
      {lines.map((line, i) => (
        <div key={i} style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
          <input className="n-input" aria-label={`${what} ${i + 1}`} style={{ flex: 1, minWidth: 0 }} value={line} placeholder={placeholder}
            disabled={!canWrite} onChange={(e) => onChange(lines.map((x, j) => (j === i ? e.target.value : x)))} />
          {canWrite && (
            <>
              <MoveButtons index={i} count={lines.length} what={`${what} ${i + 1}`} onMove={(d) => onChange(moveRow(lines, i, d))} />
              <IconButton icon={Trash2} iconSize={16} label={`Remove ${what} ${i + 1}`} onClick={() => onChange(lines.filter((_, j) => j !== i))} />
            </>
          )}
        </div>
      ))}
      {canWrite && (
        <Button size="sm" variant="quiet" icon={Plus} style={{ alignSelf: 'flex-start' }} onClick={() => onChange([...lines, ''])}>
          Add {what}
        </Button>
      )}
    </div>
  );
}

/** Pick a brand image: upload one (the logo) or choose from the library. */
function BrandImageField({ label, fileId, images, canWrite, onChange, upload = false, hint }: {
  label: string;
  fileId: string | null;
  images: LibraryImage[];
  canWrite: boolean;
  onChange: (fileId: string | null) => void;
  upload?: boolean;
  hint?: string;
}) {
  const id = useId();
  const uploads = useUploads(async (file, onProgress) => {
    const stored = await uploadImage<FnFile>(file, onProgress);
    onChange(stored.id);
  });
  const inLibrary = images.some((i) => i.file_id === fileId);
  return (
    <div style={{ minWidth: 0 }}>
      <label className="n-label" htmlFor={`${id}-pick`}>{label}</label>
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        {fileId ? (
          <AuthImage fileId={fileId} alt={label} width={upload ? 120 : 72} height={upload ? 60 : 48} fit="contain" style={{ background: 'var(--bg)' }} />
        ) : (
          <span style={{ ...muted, minWidth: 60 }}>None</span>
        )}
        <select
          id={`${id}-pick`}
          className="n-select"
          style={{ flex: '1 1 160px', minWidth: 0 }}
          value={fileId && inLibrary ? fileId : fileId ? '__current' : ''}
          disabled={!canWrite}
          onChange={(e) => {
            if (e.target.value !== '__current') onChange(e.target.value || null);
          }}
        >
          <option value="">{fileId ? 'Remove' : 'Choose from the library…'}</option>
          {fileId && !inLibrary && <option value="__current">{upload ? 'Uploaded image' : 'Current image'}</option>}
          {images.map((img) => <option key={img.id} value={img.file_id}>{img.name}</option>)}
        </select>
        {upload && canWrite && <UploadButton label="Upload" multiple={false} onFiles={uploads.start} />}
      </div>
      <UploadList items={uploads.items} onDismiss={uploads.dismiss} />
      {hint && <p style={help}>{hint}</p>}
    </div>
  );
}
