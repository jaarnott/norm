'use client';

import { useCallback, useEffect, useState } from 'react';
import { apiFetch } from '../lib/api';

/** Fired after a hire/retire or an App switch so every consumer refetches. */
export const TEAM_CHANGED_EVENT = 'norm:team-changed';

export interface TeamConnection {
  connector: string;
  display_name: string;
}

export interface TeamTool {
  key: string;
  label: string;
  description: string;
  writes: boolean;
  /** consolidator | backend | raw | norm function | CB tool */
  type: string;
  exists?: boolean;
  engine_only?: boolean;
}

export interface TeamComponent {
  key: string;
  label: string;
  description: string;
  page: { id: string; label: string; icon?: string } | null;
  /** Norm Core only: gives every member a menu item */
  shared?: boolean;
  /** App-platform components only (declared in the app's own version): does
   *  it get the app's menu item, and the inputs Norm can open it at */
  app_page?: boolean;
  inputs?: { name: string; description: string }[];
}

export interface TeamApp {
  slug: string;
  /** custom apps only: the App-platform slug (open/publish use it) */
  app_slug?: string;
  name: string;
  description?: string | null;
  icon?: string | null;
  /** author marker only: app = Norm's catalog, custom = built by your team,
   *  community = published by another org */
  kind: 'app' | 'custom' | 'community';
  /** the ONE team member it's bound to; '*' = every member (Norm Core) */
  member: string | null;
  bound_to_all: boolean;
  /** false = always on while its member is hired */
  switchable: boolean;
  bundled: boolean;
  price_cents: number;
  status?: string;
  enabled: boolean;
  bound_to: string[];
  pages: { id: string; label: string; icon?: string }[];
  components: TeamComponent[];
  tools: TeamTool[];
  skills: { slug: string; label: string; enabled?: boolean }[];
  /** App-platform components: the collections of data they keep */
  data: string[];
  app_platform: boolean;
  required_connections: TeamConnection[];
}

export interface IncludedMember {
  slug: string;
  name: string;
  tagline: string;
  apps: TeamApp[];
  works_with: string[];
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
  /** always-included members (Reports, App Builder) with their Apps */
  included: IncludedMember[];
  /** every App, flattened, each naming who it's bound to */
  apps: TeamApp[];
  alwaysIncluded: string[];
  /** null = everything on (fail-open: gating inactive, or the fetch failed). */
  hired: Set<string> | null;
  /** null = everything on. App slugs that are ON (member hired ∧ app enabled). */
  appsOn: Set<string> | null;
  /** page id -> the member whose menu it belongs in (from the catalog, the
   *  source of truth for placement; Norm Core pages keep their own member). */
  pageMember: Record<string, string>;
  refresh: () => void;
}

/**
 * One fetch of GET /api/team drives the sidebar's hired tabs, the page list's
 * App gate, and the team page itself. Fail-open by construction: any error or
 * an inactive gate yields hired/appsOn = null, which every consumer must read
 * as "show everything" — the UI mirror of the server's dark-launch contract.
 */
function pageMemberOf(apps: TeamApp[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const a of apps) {
    if (!a.member || a.member === '*') continue;
    for (const pg of a.pages ?? []) out[pg.id] = a.member;
  }
  return out;
}

export function useTeam(token: string | null): TeamState {
  const [state, setState] = useState<Omit<TeamState, 'refresh'>>({
    loaded: false,
    gatingActive: false,
    members: [],
    included: [],
    apps: [],
    alwaysIncluded: [],
    hired: null,
    appsOn: null,
    pageMember: {},
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
            included: d?.included ?? [],
            apps: d?.apps ?? [],
            alwaysIncluded: d?.always_included ?? [],
            hired: null,
            appsOn: null,
            pageMember: pageMemberOf(d?.apps ?? []),
          }));
          return;
        }
        const members: TeamMember[] = d.members ?? [];
        const apps: TeamApp[] = d.apps ?? [];
        const hired = new Set<string>(d.always_included ?? []);
        for (const m of members) if (m.hired) hired.add(m.slug);
        // The server's `enabled` already folds in "is its member hired".
        const appsOn = new Set<string>(apps.filter((a) => a.enabled).map((a) => a.slug));
        setState({
          loaded: true,
          gatingActive: true,
          pageMember: pageMemberOf(apps),
          members,
          included: d.included ?? [],
          apps,
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
