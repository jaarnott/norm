'use client';

import { Store } from 'lucide-react';
import Icon from './Icon';

export interface VenueOption {
  id: string;
  name: string;
}

/**
 * The venue picker every page header uses: a native <select> (keyboard and
 * screen-reader friendly) styled by tokens.css .n-select, with a store icon.
 * Presentational — the page owns the venue list and the remembered choice.
 */
export default function VenueSelect({ venues, value, onChange, allowAll = false, label = 'Venue', testId }: {
  venues: VenueOption[];
  value: string | null | undefined;
  onChange: (venueId: string) => void;
  /** Adds an "All venues" option with the value 'all'. */
  allowAll?: boolean;
  label?: string;
  testId?: string;
}) {
  return (
    <span style={{ position: 'relative', display: 'inline-flex', maxWidth: '100%' }}>
      <Icon icon={Store} size={16} tone="muted" style={{ position: 'absolute', left: 11, top: '50%', transform: 'translateY(-50%)', pointerEvents: 'none' }} />
      <select
        aria-label={label}
        className="n-select n-select--with-icon"
        value={value ?? ''}
        onChange={(e) => onChange(e.target.value)}
        data-testid={testId}
        style={{ maxWidth: '100%' }}
      >
        {!value && !allowAll && <option value="">Select venue</option>}
        {allowAll && <option value="all">All venues</option>}
        {venues.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
      </select>
    </span>
  );
}
