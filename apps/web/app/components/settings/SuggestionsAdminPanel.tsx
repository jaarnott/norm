'use client';

/**
 * Settings → AI team → Suggestions: how Home's suggestions are made, and the
 * one place to tune them — site-wide, for every organisation and venue
 * (docs/plans/suggestions-config.md). Platform admins only.
 *
 * Opens with how suggestions are picked (in words, from the live settings) and
 * a preview of what any person would see now; then the ranking and the words;
 * then a section for each team member with its prompts and the insights that
 * reach its chats.
 */

import { useEffect, useState } from 'react';
import { AGENTS } from '../layout/Sidebar';
import Icon from '../ui/Icon';
import PageState from '../ui/PageState';
import { call } from './suggestions/api';
import { muted } from './suggestions/bits';
import DetectorCard from './suggestions/DetectorCard';
import { howItPicks } from './suggestions/format';
import PreviewCard from './suggestions/PreviewCard';
import PromptList from './suggestions/PromptList';
import SettingsCard from './suggestions/SettingsCard';
import type { SuggestionAdmin } from './suggestions/types';
import WordingCard from './suggestions/WordingCard';

const link: React.CSSProperties = { color: 'var(--text-soft)', textDecoration: 'underline', textDecorationColor: 'var(--line-strong)', textUnderlineOffset: 3 };

export default function SuggestionsAdminPanel() {
  const [data, setData] = useState<SuggestionAdmin | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Bumped after every save: the page reloads what the server now holds.
  const [rev, setRev] = useState(0);
  const load = () => setRev(r => r + 1);

  useEffect(() => {
    let live = true;
    call<SuggestionAdmin>('GET', '').then(res => {
      if (!live) return;
      if (res.ok && res.data) {
        setData(res.data);
        setError(null);
      } else setError(res.problems?.[0] ?? 'Couldn’t load the suggestions.');
    });
    return () => { live = false; };
  }, [rev]);

  const header = (
    <>
      <h2 style={{ margin: 0, fontSize: 'var(--fs-lg)', fontWeight: 600, lineHeight: 1.3, color: 'var(--text)' }}>Suggestions</h2>
      <p style={{ margin: '4px 0 16px', fontSize: 'var(--fs-sm)', color: 'var(--muted)' }}>
        The prompts Home suggests under the chat box. One set for every organisation and venue — changes here are site-wide.
      </p>
    </>
  );
  if (error) return <div>{header}<PageState kind="error" title={error} /></div>;
  if (!data) return <div>{header}<PageState kind="loading" title="Loading…" /></div>;

  const sections = AGENTS.map(a => ({
    ...a,
    detectors: data.detectors.filter(d => (a.id === 'home' ? d.members.length === 0 : d.members.includes(a.id))),
    prompts: data.prompts.filter(p => p.member === a.id || (a.id === 'home' && p.member === 'norm')),
  }));

  return (
    <div data-testid="suggestions-admin" style={{ lineHeight: 1.45 }}>
      {header}
      <section className="n-card" style={{ padding: '14px 18px', marginBottom: 16 }} aria-label="How suggestions are picked">
        <h3 style={{ margin: 0, fontSize: 'var(--fs-base)', fontWeight: 600 }}>How Norm picks suggestions</h3>
        <ul style={{ margin: '6px 0 0', paddingLeft: 18, fontSize: 'var(--fs-sm)', color: 'var(--text-soft)' }}>
          {howItPicks(data.settings).map((line, i) => <li key={i} style={{ margin: '3px 0' }}>{line}</li>)}
        </ul>
        <nav aria-label="Sections" style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 12px', marginTop: 10, fontSize: 'var(--fs-sm)' }}>
          {['preview', 'ranking', 'words'].map(id => <a key={id} href={`#sugg-sec-${id}`} style={link}>{id[0].toUpperCase() + id.slice(1)}</a>)}
          {sections.map(s => <a key={s.id} href={`#sugg-member-${s.id}`} style={link}>{s.id === 'home' ? 'Home' : s.label}</a>)}
        </nav>
      </section>
      <div id="sugg-sec-preview"><PreviewCard /></div>
      <div id="sugg-sec-ranking"><SettingsCard settings={data.settings} meta={data.settings_meta} onSaved={load} /></div>
      <div id="sugg-sec-words">
        <WordingCard settings={data.settings} meta={data.settings_meta} placeholders={data.guidance_placeholders} version={data.wording_version} onSaved={load} />
      </div>
      {sections.map(s => (
        <section key={s.id} id={`sugg-member-${s.id}`} className="n-card" style={{ padding: '14px 18px', marginBottom: 16 }} aria-label={s.label}>
          <h3 style={{ margin: 0, display: 'flex', alignItems: 'center', gap: 8, fontSize: 'var(--fs-base)', fontWeight: 600 }}>
            <Icon icon={s.icon} size={16} tone="muted" />
            {s.id === 'home' ? 'Home' : s.label}
          </h3>
          <p style={{ ...muted, margin: '2px 0 0' }}>
            {s.id === 'home'
              ? 'Home shows every team member’s prompts and every insight. Here: Norm’s own prompts, and the insights that show on Home only.'
              : `What ${s.label}’s new chat suggests (Home shows these too).`}
          </p>
          <PromptList member={s.id} admin={data} onSaved={load} />
          <h4 style={{ margin: '14px 0 6px', fontSize: 'var(--fs-sm)', fontWeight: 600, color: 'var(--text)' }}>Insights</h4>
          {s.detectors.length === 0
            ? <p style={muted}>No insights reach this member&apos;s chats. Tick it on a detector to add one.</p>
            : s.detectors.map(d => <DetectorCard key={d.name} d={d} onSaved={load} />)}
        </section>
      ))}
    </div>
  );
}
