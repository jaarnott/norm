'use client';

import Composer from '../chat/Composer';
import type { SendOptions } from '../chat/AttachmentComposer';
import Suggestions from './Suggestions';
import TodayBlock from './TodayBlock';
import type { TodayItem } from '../../types';
import { firstNameOf, timeOfDayWord } from '../../hooks/useAccess';

interface HomePanelProps {
  onSend: (message: string, opts?: SendOptions) => void;
  loading: boolean;
  /** The team member whose new chat this is ('home' for everyone's). */
  member?: string;
  /** The venue the person is working in — suggestions name it. */
  venueId?: string | null;
  /** Four suggestions on a desktop, two on a phone. */
  suggestions?: number;
  /** Venue staff (a role with no AI agent): the heading is "Today" with a
   *  greeting, and the box says "Ask Norm…" (docs/plans/staff-access.md 11.7). */
  staff?: boolean;
  /** false on a kitchen tablet: no composer and no suggestions — a tablet
   *  never chats (docs/plans/staff-access.md, section 5). Venue staff
   *  (`staff`) keep the composer and lose the suggestions. */
  chat?: boolean;
  /** Who is signed in, for the greeting. */
  userName?: string | null;
  /** What is due today (GET /api/me/today); the block shows only with items. */
  today?: TodayItem[];
  onOpenToday?: (item: TodayItem) => void;
}

export default function HomePanel({ onSend, loading, member, venueId, suggestions = 4, staff = false, userName, today = [], onOpenToday, chat = true }: HomePanelProps) {
  const first = firstNameOf(userName);
  const greeting = `${timeOfDayWord()}${first ? `, ${first}` : ''}`;
  // Suggestions are picked per AI agent; the apps section asks for Norm's.
  const suggestionMember = member === 'apps' ? 'home' : member;
  // Three rows: equal space above and below keeps the title and the box
  // centred on their own, and the suggestions fill the space below. They
  // arrive after the page does, so if they were part of the centred block the
  // box would jump up when they loaded (and back down on each dismiss).
  // With a Today block the middle row grows with it, so the page starts
  // nearer the top instead of centring a long list.
  return (
    <div style={{
      height: '100%',
      display: 'grid',
      gridTemplateRows: today.length ? 'minmax(16px, 0.4fr) auto minmax(0, 1fr)' : 'minmax(0, 1fr) auto minmax(0, 1fr)',
      justifyItems: 'center',
      padding: '0 16px',
      overflowY: 'auto',
      backgroundColor: 'var(--canvas)',
    }}>
      <div />
      <div style={{ width: '100%', maxWidth: 768 }}>
        <div style={{ textAlign: 'center', marginBottom: 24 }}>
          <h1 className="n-page-title" style={{ margin: '0 0 4px', fontSize: 'var(--fs-2xl)', fontWeight: 700, letterSpacing: '-0.01em', color: 'var(--text)' }}>
            {staff ? 'Today' : 'Norm'}
          </h1>
          <div data-testid="home-greeting" style={{ fontSize: 'var(--fs-md)', color: 'var(--muted)' }}>
            {staff ? greeting : 'AI Operations Control — What would you like to do?'}
          </div>
        </div>

        <TodayBlock items={today} title={staff ? undefined : 'Today at your venues'} onOpen={onOpenToday} />

        {chat && (
          <Composer
            onSend={(text, attachments) => onSend(text, { attachments })}
            loading={loading}
            inputTestId="home-message-input"
            sendTestId="home-send-btn"
            placeholder={staff ? 'Ask Norm…' : undefined}
          />
        )}
      </div>

      <div style={{ width: '100%', maxWidth: 768, minHeight: 0, overflowY: 'auto' }}>
        {/* Suggestions are a manager's (costs, building apps); venue staff
            have the Today block and the box. */}
        {chat && !staff && (
          <Suggestions
            member={suggestionMember}
            venueId={venueId}
            limit={suggestions}
            onPick={s => onSend(s.prompt, { suggestionId: s.id })}
          />
        )}
      </div>
    </div>
  );
}
