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
import OrdersDashboard from './OrdersDashboard';
import Chart from './Chart';
import ReportBuilder from './ReportBuilder';
import SavedReportsBoard from './SavedReportsBoard';
import ToolApprovalCard from './ToolApprovalCard';
import ReceiveInvoiceEditor from './ReceiveInvoiceEditor';
import InvoicesDashboard from './InvoicesDashboard';
import MenuEditor from './MenuEditor';
import DashboardView from './DashboardView';
import VenuePicker from './VenuePicker';
import StockPicker from './StockPicker';
import McpEmbed from './McpEmbed';
import ConnectorConnectCard from './ConnectorConnectCard';

export interface DisplayBlockProps {
  data: Record<string, unknown>;
  props?: Record<string, unknown>;
  onAction?: (action: WidgetAction) => Promise<Record<string, unknown> | void>;
  threadId?: string;
}

/** Components that render full-width above the conversation instead of inline in chat bubbles */
export const FULL_WIDTH_COMPONENTS = new Set(['roster_editor', 'hiring_board', 'report_builder', 'orders_dashboard', 'invoices_dashboard', 'menu_editor', 'dashboard_view']);

// The single source of truth for which display components EXIST. The admin
// Settings → Components panel derives its catalogue from these keys (with a
// metadata overlay), so registering a component here surfaces it there
// automatically — never maintain a second hand-written list.
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
  orders_dashboard: OrdersDashboard,
  invoices_dashboard: InvoicesDashboard,
  menu_editor: MenuEditor,
  tool_approval: ToolApprovalCard,
  receive_invoice_editor: ReceiveInvoiceEditor,
  venue_picker: VenuePicker,
  stock_picker: StockPicker,
  dashboard_view: DashboardView,
  mcp_embed: McpEmbed,
  connector_connect: ConnectorConnectCard,
};

/** Every registered display-component key — what the admin catalogue derives from. */
export const REGISTERED_COMPONENTS: string[] = Object.keys(REGISTRY);

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
