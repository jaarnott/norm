'use client';

import type { DisplayBlock, WidgetAction } from '../../types';
import GenericTable from './GenericTable';
import RosterTable from './RosterTable';
import PurchaseOrder from './PurchaseOrder';
import RosterEditor from './RosterEditor';
import PurchaseOrderEditor from './PurchaseOrderEditor';

export interface DisplayBlockProps {
  data: Record<string, unknown>;
  props?: Record<string, unknown>;
  onAction?: (action: WidgetAction) => Promise<Record<string, unknown> | void>;
  taskId?: string;
}

/** Components that render full-width above the conversation instead of inline in chat bubbles */
export const FULL_WIDTH_COMPONENTS = new Set(['roster_editor']);

const REGISTRY: Record<string, React.ComponentType<DisplayBlockProps>> = {
  generic_table: GenericTable,
  roster_table: RosterTable,
  purchase_order: PurchaseOrder,
  purchase_order_editor: PurchaseOrderEditor,
  roster_editor: RosterEditor,
};

interface DisplayBlockRendererProps {
  block: DisplayBlock;
  onAction?: (action: WidgetAction) => Promise<Record<string, unknown> | void>;
  taskId?: string;
}

export default function DisplayBlockRenderer({ block, onAction, taskId }: DisplayBlockRendererProps) {
  const Component = REGISTRY[block.component];
  if (!Component) return null;
  return <Component data={block.data} props={block.props} onAction={onAction} taskId={taskId} />;
}
