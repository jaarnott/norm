'use client';

import Composer from '../chat/Composer';
import type { SendOptions } from '../chat/AttachmentComposer';
import Suggestions from './Suggestions';

interface HomePanelProps {
  onSend: (message: string, opts?: SendOptions) => void;
  loading: boolean;
  /** The team member whose new chat this is ('home' for everyone's). */
  member?: string;
  /** The venue the person is working in — suggestions name it. */
  venueId?: string | null;
  /** Four suggestions on a desktop, two on a phone. */
  suggestions?: number;
}

export default function HomePanel({ onSend, loading, member, venueId, suggestions = 4 }: HomePanelProps) {
  return (
    <div style={{
      height: '100%',
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: 'center',
      padding: '0 16px',
      backgroundColor: 'var(--canvas)',
    }}>
      <div style={{ textAlign: 'center', marginBottom: 24 }}>
        <h1 className="n-page-title" style={{ margin: '0 0 4px', fontSize: 'var(--fs-2xl)', fontWeight: 700, letterSpacing: '-0.01em', color: 'var(--text)' }}>
          Norm
        </h1>
        <div style={{ fontSize: 'var(--fs-md)', color: 'var(--muted)' }}>
          AI Operations Control — What would you like to do?
        </div>
      </div>

      <div style={{ width: '100%', maxWidth: 768 }}>
        <Composer
          onSend={(text, attachments) => onSend(text, { attachments })}
          loading={loading}
          inputTestId="home-message-input"
          sendTestId="home-send-btn"
        />
        <Suggestions
          member={member}
          venueId={venueId}
          limit={suggestions}
          onPick={s => onSend(s.prompt, { suggestionId: s.id })}
        />
      </div>
    </div>
  );
}
