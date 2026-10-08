'use client';

import { useState, useEffect, useRef } from 'react';
import tzlookup from '@photostructure/tz-lookup';
import { LoaderCircle } from 'lucide-react';
import Icon from '../ui/Icon';

interface NominatimResult {
  place_id: number;
  display_name: string;
  lat: string;
  lon: string;
}

export interface AddressSelection {
  address: string;
  lat: number;
  lon: number;
  timezone: string | null;
}

/**
 * Address autocomplete backed by OpenStreetMap Nominatim.
 * On selection, resolves the IANA timezone from the coordinates.
 */
export default function AddressSearch({
  value,
  onChange,
  onSelect,
  placeholder,
  inputStyle,
}: {
  value: string;
  onChange: (value: string) => void;
  onSelect: (selection: AddressSelection) => void;
  placeholder?: string;
  inputStyle?: React.CSSProperties;
}) {
  const [results, setResults] = useState<NominatimResult[]>([]);
  const [open, setOpen] = useState(false);
  const [searching, setSearching] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const skipNextSearch = useRef(false);

  useEffect(() => {
    if (skipNextSearch.current) {
      skipNextSearch.current = false;
      return;
    }
    const query = value.trim();
    if (query.length < 3) {
      setResults([]);
      setOpen(false);
      return;
    }
    const timer = setTimeout(async () => {
      setSearching(true);
      try {
        const res = await fetch(
          `https://nominatim.openstreetmap.org/search?format=jsonv2&limit=5&q=${encodeURIComponent(query)}`,
          { headers: { Accept: 'application/json' } },
        );
        if (res.ok) {
          const data: NominatimResult[] = await res.json();
          setResults(data);
          setOpen(data.length > 0);
        }
      } catch { /* ignore — network errors just mean no suggestions */ }
      setSearching(false);
    }, 400);
    return () => clearTimeout(timer);
  }, [value]);

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const handlePick = (r: NominatimResult) => {
    const lat = parseFloat(r.lat);
    const lon = parseFloat(r.lon);
    let timezone: string | null = null;
    try {
      timezone = tzlookup(lat, lon);
    } catch { /* out-of-range coords — leave timezone unset */ }
    skipNextSearch.current = true;
    setOpen(false);
    setResults([]);
    onChange(r.display_name);
    onSelect({ address: r.display_name, lat, lon, timezone });
  };

  // The field is an .n-input; a caller's inputStyle still layers on top.
  // Suggestions: a white panel of .n-option rows (hover comes from the class).
  return (
    <div ref={containerRef} style={{ position: 'relative' }}>
      <input
        className="n-input"
        value={value}
        onChange={e => onChange(e.target.value)}
        onFocus={() => { if (results.length > 0) setOpen(true); }}
        placeholder={placeholder || 'Search address…'}
        style={{ width: '100%', ...inputStyle }}
      />
      {searching && (
        <span style={{
          position: 'absolute', right: 10, top: '50%', transform: 'translateY(-50%)',
          display: 'inline-flex', pointerEvents: 'none',
        }}>
          <Icon icon={LoaderCircle} size="dense" tone="muted" style={{ animation: 'n-spin 1s linear infinite' }} />
        </span>
      )}
      {open && results.length > 0 && (
        <div className="scroll-quiet" style={{
          position: 'absolute', top: '100%', left: 0, right: 0, zIndex: 50,
          marginTop: 4, padding: '4px 0', maxHeight: 240,
          background: 'var(--bg)', border: '1px solid var(--line)',
          borderRadius: 'var(--radius)', boxShadow: '0 6px 18px rgba(26,26,26,0.12)',
        }}>
          {results.map(r => (
            <div
              key={r.place_id}
              className="n-option"
              onClick={() => handlePick(r)}
              style={{ padding: '8px 12px', lineHeight: 1.35 }}
            >
              {r.display_name}
            </div>
          ))}
          <div style={{ margin: '4px 0 0', padding: '6px 12px 2px', borderTop: '1px solid var(--line-soft)', fontSize: 'var(--fs-2xs)', color: 'var(--muted)' }}>
            © OpenStreetMap
          </div>
        </div>
      )}
    </div>
  );
}
