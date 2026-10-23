'use client';

import type { DisplayBlock, WidgetAction } from '../../types';
import GenericTable from './GenericTable';
import AppsDashboard from '../apps/AppsDashboard';
import AppPage from '../apps/AppPage';
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
import RecipeEditor from './RecipeEditor';
import MenuEngineering from './MenuEngineering';
import SupplierTenders from './SupplierTenders';
import StockCatalogue from './catalogue/StockCatalogue';
import XeroBills from './XeroBills';
import StocktakesBoard from './StocktakesBoard';
import AttendanceBoard from './AttendanceBoard';
import ProfitAndLoss from './ProfitAndLoss';
import EmployeeRecords from './EmployeeRecords';
import SalesAnalysis from './SalesAnalysis';
import WeeklyReport from './WeeklyReport';
import DashboardView from './DashboardView';
import VenuePicker from './VenuePicker';
import StockPicker from './StockPicker';
import McpEmbed from './McpEmbed';
import ConnectorConnectCard from './ConnectorConnectCard';
import BackgroundJobCard from './BackgroundJobCard';
import FinanceWeekly from './FinanceWeekly';
import PayrollPayrun from '../finance/payroll/PayrollPayrun';
import PayrollLeave from '../finance/payroll/PayrollLeave';
import PayrollIntercompany from '../finance/payroll/PayrollIntercompany';
import PayrollReconciliation from '../finance/payroll/PayrollReconciliation';
import PayrollSettings from '../finance/payroll/PayrollSettings';
import PayrollShadow from '../finance/payroll/PayrollShadow';
import EntertainersRegister from '../finance/entertainers/EntertainersRegister';
import EntertainersRoster from '../finance/entertainers/EntertainersRoster';
import EntertainersPayments from '../finance/entertainers/EntertainersPayments';
import EntertainersEmails from '../finance/entertainers/EntertainersEmails';
import FinanceSummary from '../finance/finance/FinanceSummary';
import FinanceBudgets from '../finance/finance/FinanceBudgets';
import FinanceBudgetVsLoaded from '../finance/finance/FinanceBudgetVsLoaded';
import FinanceForecasting from '../finance/finance/FinanceForecasting';
import FunctionsDashboard from '../functions/FunctionsDashboard';
import FunctionsEnquiries from '../functions/FunctionsEnquiries';
import FunctionsEvents from '../functions/FunctionsEvents';
import FunctionsInbox from '../functions/FunctionsInbox';
import FunctionsContacts from '../functions/FunctionsContacts';
import FunctionsReports from '../functions/FunctionsReports';
import FunctionsSettings from '../functions/FunctionsSettings';

export interface DisplayBlockProps {
  data: Record<string, unknown>;
  props?: Record<string, unknown>;
  onAction?: (action: WidgetAction) => Promise<Record<string, unknown> | void>;
  threadId?: string;
}

/**
 * Components that render full-width above the conversation instead of inline
 * in chat bubbles. Deliberately web-owned (NOT read from the marketplace
 * catalog): layout is decided synchronously during chat render, and an async
 * catalog fetch there would flicker. The catalog compositions carry a
 * `full_width` copy for documentation only — nothing reads it; this Set is
 * the authority.
 */
// recipe_editor left this set in Oct 2026: the thread shows ONE full-width
// block (the latest), and a draft_recipe call opens one card per recipe —
// they need to sit inline, each its own card. The Recipes page is unaffected
// (it is a functional page, not a block).
export const FULL_WIDTH_COMPONENTS = new Set(['roster_editor', 'hiring_board', 'report_builder', 'orders_dashboard', 'invoices_dashboard', 'menu_editor', 'menu_engineering', 'supplier_tenders', 'stock_catalogue', 'dashboard_view', 'apps_dashboard', 'app_runner', 'functions_dashboard', 'functions_enquiries', 'functions_events', 'functions_inbox', 'functions_contacts', 'functions_reports', 'functions_settings', 'xero_bills', 'finance_weekly', 'payroll_payrun', 'payroll_leave', 'payroll_intercompany', 'payroll_reconciliation', 'payroll_settings', 'payroll_shadow', 'entertainers_register', 'entertainers_roster', 'entertainers_payments', 'entertainers_emails', 'finance_summary', 'finance_budgets', 'finance_budget_vs_loaded', 'finance_forecasting', 'stocktakes_board', 'attendance_board', 'profit_and_loss', 'employee_records', 'sales_analysis', 'weekly_report']);

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
  apps_dashboard: AppsDashboard,
  app_runner: AppPage,
  menu_editor: MenuEditor,
  recipe_editor: RecipeEditor,
  menu_engineering: MenuEngineering,
  supplier_tenders: SupplierTenders,
  stock_catalogue: StockCatalogue,
  functions_dashboard: FunctionsDashboard,
  functions_enquiries: FunctionsEnquiries,
  functions_events: FunctionsEvents,
  functions_inbox: FunctionsInbox,
  functions_contacts: FunctionsContacts,
  functions_reports: FunctionsReports,
  functions_settings: FunctionsSettings,
  xero_bills: XeroBills,
  stocktakes_board: StocktakesBoard,
  attendance_board: AttendanceBoard,
  profit_and_loss: ProfitAndLoss,
  employee_records: EmployeeRecords,
  sales_analysis: SalesAnalysis,
  weekly_report: WeeklyReport,
  // Finance (Norm Finance App): the Weekly Summary block, chat + page
  finance_weekly: FinanceWeekly,
  // Finance member pages (Loaded Payroll + Norm Finance Apps), each loading its own data
  payroll_payrun: PayrollPayrun,
  payroll_leave: PayrollLeave,
  payroll_intercompany: PayrollIntercompany,
  payroll_reconciliation: PayrollReconciliation,
  payroll_settings: PayrollSettings,
  payroll_shadow: PayrollShadow,
  entertainers_register: EntertainersRegister,
  entertainers_roster: EntertainersRoster,
  entertainers_payments: EntertainersPayments,
  entertainers_emails: EntertainersEmails,
  finance_summary: FinanceSummary,
  finance_budgets: FinanceBudgets,
  finance_budget_vs_loaded: FinanceBudgetVsLoaded,
  finance_forecasting: FinanceForecasting,
  tool_approval: ToolApprovalCard,
  receive_invoice_editor: ReceiveInvoiceEditor,
  venue_picker: VenuePicker,
  stock_picker: StockPicker,
  dashboard_view: DashboardView,
  mcp_embed: McpEmbed,
  connector_connect: ConnectorConnectCard,
  background_job: BackgroundJobCard,
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
