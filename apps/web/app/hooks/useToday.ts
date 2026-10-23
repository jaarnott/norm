'use client';

import { useCallback, useEffect, useState } from 'react';
import { apiFetch } from '../lib/api';
import type { TodayItem } from '../types';
import { TEAM_CHANGED_EVENT } from './useTeam';

/**
 * What is due for this person today (GET /api/me/today): the feed for Home's
 * Today block. Loaded once the token is known and again when the team
 * changes (an app switched on brings its items with it). Nothing due, or a
 * failed load, reads as an empty list — Home never fails over this block.
 */
export function useToday(token: string | null): { items: TodayItem[]; loaded: boolean; refresh: () => void } {
  const [items, setItems] = useState<TodayItem[]>([]);
  const [loaded, setLoaded] = useState(false);

  const load = useCallback(() => {
    apiFetch('/api/me/today')
      .then(r => (r.ok ? r.json() : null))
      .then(d => setItems(Array.isArray(d?.items) ? (d.items as TodayItem[]) : []))
      .catch(() => setItems([]))
      .finally(() => setLoaded(true));
  }, []);

  useEffect(() => {
    if (!token) return;
    load();
    window.addEventListener(TEAM_CHANGED_EVENT, load);
    return () => window.removeEventListener(TEAM_CHANGED_EVENT, load);
  }, [token, load]);

  return { items, loaded, refresh: load };
}
