'use client';

import { useState, useEffect, useRef } from 'react';
import tzlookup from '@photostructure/tz-lookup';

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

  return (
    <div ref={containerRef} style={{ position: 'relative' }}>
      <input
        value={value}
        onChange={e => onChange(e.target.value)}
        onFocus={() => { if (results.length > 0) setOpen(true); }}
        placeholder={placeholder || 'Search address…'}
        style={inputStyle}
      />
      {searching && (
        <span style={{
          position: 'absolute', right: 8, top: '50%', transform: 'translateY(-50%)',
          fontSize: '0.65rem', color: '#aaa', pointerEvents: 'none',
        }}>…</span>
      )}
      {open && results.length > 0 && (
        <div style={{
          position: 'absolute', top: '100%', left: 0, right: 0, zIndex: 50,
          marginTop: 2, backgroundColor: '#fff', border: '1px solid #ddd',
          borderRadius: 6, boxShadow: '0 4px 12px rgba(0,0,0,0.08)',
          maxHeight: 220, overflowY: 'auto',
        }}>
          {results.map(r => (
            <div
              key={r.place_id}
              onClick={() => handlePick(r)}
              style={{
                padding: '6px 10px', fontSize: '0.78rem', color: '#333',
                cursor: 'pointer', borderBottom: '1px solid #f3f4f6',
              }}
              onMouseEnter={e => { (e.currentTarget as HTMLDivElement).style.backgroundColor = '#f8fafc'; }}
              onMouseLeave={e => { (e.currentTarget as HTMLDivElement).style.backgroundColor = '#fff'; }}
            >
              {r.display_name}
            </div>
          ))}
          <div style={{ padding: '3px 10px', fontSize: '0.62rem', color: '#bbb' }}>
            © OpenStreetMap
          </div>
        </div>
      )}
    </div>
  );
}
