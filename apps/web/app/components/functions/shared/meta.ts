'use client';

/**
 * Norm Functions — `GET /api/functions/meta`, loaded once and shared by every
 * Functions page (company settings, mode, the caller's venues and their
 * settings, enums). See scratchpad F3-api.md §Meta for the full shape.
 */
import { useCallback, useEffect, useState } from 'react';
import { fnGet } from './api';

export type FunctionsMode = 'off' | 'shadow' | 'live';

export interface FnCompany {
  currency: string;
  default_timezone: string;
  tax_label: string;
  functions_mode: FunctionsMode;
  name?: string;
}

export interface FnVenue {
  id: string;
  name: string;
  timezone: string | null;
  timezone_valid: boolean;
  day_start_time: string | null;
  is_approver: boolean;
  is_override_approver: boolean;
  has_brand: boolean;
  settings: Record<string, unknown> | null;
  /** enabled_fields[booking_type][field_key] — missing means on. */
  enabled_fields: Record<string, Record<string, boolean>>;
}

export interface FnMember {
  id: string;
  name: string;
  email: string;
  is_owner: boolean;
  venue_ids: string[];
}

export interface FnOption {
  value: string;
  label: string;
  [extra: string]: unknown;
}

export interface FnMeta {
  company: FnCompany;
  mode: FunctionsMode;
  can_write: boolean;
  user: { id: string; name: string; email: string; is_owner: boolean };
  venues: FnVenue[];
  members: FnMember[];
  booking_types: (FnOption & { min_spend: boolean })[];
  field_catalogue: { section: string; key: string; label: string }[];
  stages: unknown[];
  event_statuses: unknown[];
  min_spend_states: unknown[];
  next_actions: { kind: string; label: string }[];
  enquiry_sources: unknown[];
  staff_channels: string[];
  food_timing_types: FnOption[];
  entertainment_kinds: string[];
  av_kinds: string[];
  event_sections: unknown[];
  reply_template_stages: string[];
  dietary_codes: string[];
  client_fields: string[];
  reopen_rule: string;
  [extra: string]: unknown;
}

let cached: Promise<FnMeta> | null = null;

/** The shared meta promise; `force` refetches (after settings change). */
export function loadFunctionsMeta(force = false): Promise<FnMeta> {
  if (!cached || force) {
    cached = fnGet<FnMeta>('/meta').catch((err) => {
      cached = null; // let the next caller retry
      throw err;
    });
  }
  return cached;
}

export function useFunctionsMeta(): {
  meta: FnMeta | null;
  error: string | null;
  loading: boolean;
  reload: () => void;
} {
  const [meta, setMeta] = useState<FnMeta | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let alive = true;
    loadFunctionsMeta(tick > 0)
      .then((m) => {
        if (alive) {
          setMeta(m);
          setError(null);
        }
      })
      .catch((e: unknown) => {
        if (alive) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      alive = false;
    };
  }, [tick]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { meta, error, loading: !meta && !error, reload };
}

/** Is `field` enabled for this venue × booking type? Missing settings mean on. */
export function fieldEnabled(venue: FnVenue | undefined, bookingType: string | null | undefined, field: string): boolean {
  if (!venue || !bookingType) return true;
  const row = venue.enabled_fields?.[bookingType];
  if (!row || !(field in row)) return true;
  return row[field] !== false;
}

/** A URL search param from the current location (deep links such as
 *  `?page=functions-events&event=<id>`). Read once on mount. */
export function readPageParam(name: string): string | null {
  if (typeof window === 'undefined') return null;
  try {
    return new URLSearchParams(window.location.search).get(name);
  } catch {
    return null;
  }
}
