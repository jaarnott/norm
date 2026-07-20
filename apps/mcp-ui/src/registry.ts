/**
 * Norm's display-component registry for the MCP App bundle.
 *
 * These are the SAME components the web app renders — imported directly from
 * apps/web, never copied. Fix a rendering bug in one and both Norm and
 * Claude get the fix.
 *
 * Components may fetch: the build swaps `lib/api` for `sandbox-api.ts`, which
 * routes the same working-document and component-API calls through the MCP
 * host (`tools/call`) into Norm's authenticated dispatch. A component that
 * calls an endpoint the shim doesn't map gets a 404-shaped response and
 * degrades the same way it would on any constrained surface — so check how a
 * component fails, not just how it succeeds, before adding it here.
 *
 * Still excluded: anything importing next/* (no meaning outside Next), and
 * anything whose only value is a static table — Claude draws tables better
 * than an iframe does. A component earns a place here by doing something
 * Claude cannot: editing a draft, dragging a shift, placing an order.
 */
import type { ComponentType } from 'react';
import GenericTable from '../../web/app/components/display/GenericTable';
import RosterEditor from '../../web/app/components/display/RosterEditor';
import PurchaseOrderEditor from '../../web/app/components/display/PurchaseOrderEditor';
import WorkflowResult from './WorkflowResult';

// Structurally DisplayBlockProps, declared locally so we don't import
// DisplayBlockRenderer (which pulls the whole self-fetching registry).
export interface BlockProps {
  data: Record<string, unknown>;
  props?: Record<string, unknown>;
  onAction?: (action: unknown) => Promise<Record<string, unknown> | void>;
  threadId?: string;
}

export const REGISTRY: Record<string, ComponentType<BlockProps>> = {
  // Rich, interactive: the weekly drag grid / day timeline — the thing Claude
  // cannot draw itself. Renders read-only from its data; mutations delegate
  // to `onAction`, which we don't pass, so there are no dead buttons.
  roster_editor: RosterEditor as ComponentType<BlockProps>,
  // The purchase-order editor, fully interactive: lines arrive pre-resolved
  // (server-side, po_display.py), edits patch the Norm draft through
  // norm__update_working_document, and Place Order submits through
  // norm__place_stock_order — the user's click is the approval.
  purchase_order_editor: PurchaseOrderEditor as ComponentType<BlockProps>,
  // Status card for playbook outcomes that aren't a draft we can edit
  // (running / completed / pending approval).
  workflow_result: WorkflowResult,
  // Fallback only, for a component name we don't recognise. Never bound to a
  // tool: Claude renders tables better than we can embed them.
  generic_table: GenericTable as ComponentType<BlockProps>,
};
