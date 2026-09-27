'use client';

import { useCallback, useEffect, useState } from 'react';
import { apiFetch } from '../lib/api';

/** Fired after a hire/retire or an App switch so every consumer refetches. */
export const TEAM_CHANGED_EVENT = 'norm:team-changed';

export interface TeamConnection {
  connector: string;
  display_name: string;
}

export interface TeamApp {
  slug: string;
  name: string;
  description?: string | null;
  bundled: boolean;
  price_cents: number;
  enabled: boolean;
  pages: { id: string; label: string; icon?: string }[];
  required_connections: TeamConnection[];
}

export interface TeamMember {
  slug: string;
  catalog_slug: string;
  name: string;
  icon?: string | null;
  tagline?: string | null;
  hireable: boolean;
  hired: boolean;
  enabled_explicitly: boolean;
  price_cents: number;
  apps: TeamApp[];
  works_with: string[];
}

export interface TeamState {
  loaded: boolean;
  /** false = hierarchy-v2 gating not active (pre-rollout catalog): show everything. */
  gatingActive: boolean;
  members: TeamMember[];
  alwaysIncluded: string[];
  /** null = everything on (fail-open: gating inactive, or the fetch failed). */
  hired: Set<string> | null;
  /** null = everything on. App slugs that are ON (member hired ∧ app enabled). */
  appsOn: Set<string> | null;
  refresh: () => void;
}

/**
 * One fetch of GET /api/team drives the sidebar's hired tabs, the page list's
 * App gate, and the team page itself. Fail-open by construction: any error or
 * an inactive gate yields hired/appsOn = null, which every consumer must read
 * as "show everything" — the UI mirror of the server's dark-launch contract.
 */
export function useTeam(token: string | null): TeamState {
  const [state, setState] = useState<Omit<TeamState, 'refresh'>>({
    loaded: false,
    gatingActive: false,
    members: [],
    alwaysIncluded: [],
    hired: null,
    appsOn: null,
  });

  const load = useCallback(() => {
    apiFetch('/api/team')
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (!d || !d.gating_active) {
          setState((s) => ({
            ...s,
            loaded: true,
            gatingActive: false,
            members: d?.members ?? [],
            alwaysIncluded: d?.always_included ?? [],
            hired: null,
            appsOn: null,
          }));
          return;
        }
        const members: TeamMember[] = d.members ?? [];
        const hired = new Set<string>(d.always_included ?? []);
        const appsOn = new Set<string>();
        for (const m of members) {
          if (m.hired) {
            hired.add(m.slug);
            for (const a of m.apps) if (a.enabled) appsOn.add(a.slug);
          }
        }
        setState({
          loaded: true,
          gatingActive: true,
          members,
          alwaysIncluded: d.always_included ?? [],
          hired,
          appsOn,
        });
      })
      .catch(() => setState((s) => ({ ...s, loaded: true, hired: null, appsOn: null })));
  }, []);

  useEffect(() => {
    if (!token) return;
    load();
    window.addEventListener(TEAM_CHANGED_EVENT, load);
    return () => window.removeEventListener(TEAM_CHANGED_EVENT, load);
  }, [token, load]);

  return { ...state, refresh: load };
}
