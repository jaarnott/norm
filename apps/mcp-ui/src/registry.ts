/**
 * Sandbox-safe subset of Norm's display-component registry.
 *
 * These are the SAME components the web app renders — imported directly from
 * apps/web, never copied. Fix a rendering bug in RosterTable and both Norm and
 * Claude get the fix.
 *
 * It is a subset, not the whole registry, for one hard reason: an MCP App runs
 * in a sandboxed iframe with no Norm session and no network back to the API.
 * Components that fetch on mount (RosterEditor, PurchaseOrderEditor,
 * OrdersDashboard, DashboardView, ReportBuilder, …) would mount and immediately
 * fail, and DashboardView additionally pulls `next/dynamic`, which has no
 * meaning outside Next. Only components that are pure functions of their
 * `data`/`props` can appear here.
 *
 * Adding one: confirm it has no useEffect fetch, no apiFetch/callComponentApi
 * on mount, and no next/* import. Then map a tool to it in app/mcp/ui_apps.py.
 */
import type { ComponentType } from 'react';
import GenericTable from '../../web/app/components/display/GenericTable';
import RosterTable from '../../web/app/components/display/RosterTable';

// Structurally DisplayBlockProps, declared locally so we don't import
// DisplayBlockRenderer (which pulls the whole self-fetching registry).
export interface BlockProps {
  data: Record<string, unknown>;
  props?: Record<string, unknown>;
  onAction?: (action: unknown) => Promise<Record<string, unknown> | void>;
  threadId?: string;
}

export const REGISTRY: Record<string, ComponentType<BlockProps>> = {
  generic_table: GenericTable as ComponentType<BlockProps>,
  roster_table: RosterTable as ComponentType<BlockProps>,
};
