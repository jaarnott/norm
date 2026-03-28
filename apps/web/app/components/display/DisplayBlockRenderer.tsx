'use client';

import type { DisplayBlock, WidgetAction } from '../../types';
import GenericTable from './GenericTable';
import RosterTable from './RosterTable';
import RosterEditor from './RosterEditor';
import PurchaseOrderEditor from './PurchaseOrderEditor';
import CriteriaEditor from './CriteriaEditor';
import HiringBoard from './HiringBoard';
import AutomatedTaskPreview from './AutomatedTaskPreview';
import AutomatedTaskBoard from './AutomatedTaskBoard';
import Chart from './Chart';
import ReportBuilder from './ReportBuilder';
import SavedReportsBoard from './SavedReportsBoard';
import ToolApprovalCard from './ToolApprovalCard';

export interface DisplayBlockProps {
  data: Record<string, unknown>;
  props?: Record<string, unknown>;
  onAction?: (action: WidgetAction) => Promise<Record<string, unknown> | void>;
  threadId?: string;
}

/** Components that render full-width above the conversation instead of inline in chat bubbles */
export const FULL_WIDTH_COMPONENTS = new Set(['roster_editor', 'hiring_board', 'report_builder']);

const REGISTRY: Record<string, React.ComponentType<DisplayBlockProps>> = {
  generic_table: GenericTable,
  roster_table: RosterTable,
  purchase_order_editor: PurchaseOrderEditor,
  roster_editor: RosterEditor,
  criteria_editor: CriteriaEditor,
  hiring_board: HiringBoard,
  automated_task_preview: AutomatedTaskPreview,
  automated_task_board: AutomatedTaskBoard,
  chart: Chart,
  report_builder: ReportBuilder,
  saved_reports_board: SavedReportsBoard,
  tool_approval: ToolApprovalCard,
};

interface DisplayBlockRendererProps {
  block: DisplayBlock;
  onAction?: (action: WidgetAction) => Promise<Record<string, unknown> | void>;
  threadId?: string;
}

export default function DisplayBlockRenderer({ block, onAction, threadId }: DisplayBlockRendererProps) {
  const Component = REGISTRY[block.component];
  if (!Component) return null;
  return <Component data={block.data} props={block.props} onAction={onAction} threadId={threadId} />;
}
