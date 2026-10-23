// How the marketplace looks: each agent's colour and photo, each system's
// logo — and a copy of the catalog's apps, shown only if Norm's live catalog
// can't be reached (catalog.ts reads it; the catalog is the source).
//
// Every app is named after its job and filed under the AI agent that uses it.
// Only apps that work in Norm today are listed: an app whose tools aren't
// built yet (Bidfood ordering, Brevo, Metricool, Payroll) waits until they
// are. The names here are the marketing names; Norm's own catalog still
// groups some of them (Loaded Stock holds Ordering, Invoice Receiving,
// Stocktakes and Supplier Tenders) — docs/plans/marketplace-and-partner-apps.md.

export type ConnectionKey = 'loaded' | 'xero' | 'bamboohr' | 'hospo-hr' | 'hospo-functions';

export interface Connection {
  key: ConnectionKey;
  name: string;
  /** One line on what the system is. */
  what: string;
  /** A logo in public/marketing/logos, or a Hospo mark drawn in CSS. */
  logo?: string;
  /** Listed under Connections (the Hospo apps are named on cards only). */
  listed: boolean;
}

export const CONNECTIONS: Record<ConnectionKey, Connection> = {
  loaded: { key: 'loaded', name: 'Loaded', what: 'Stock, rosters and reporting', logo: '/marketing/logos/loaded.png', listed: true },
  xero: { key: 'xero', name: 'Xero', what: 'Accounting', logo: '/marketing/logos/xero.png', listed: true },
  bamboohr: { key: 'bamboohr', name: 'BambooHR', what: 'HR and employee records', logo: '/marketing/logos/bamboohr.png', listed: true },
  'hospo-hr': { key: 'hospo-hr', name: 'Hospo HR', what: 'Hiring, training and reviews', listed: false },
  'hospo-functions': { key: 'hospo-functions', name: 'Hospo Functions', what: 'Function enquiries and events', listed: false },
};

export interface App {
  slug: string;
  name: string;
  /** Who makes it. None for an app a venue built and published: the page
   *  says a venue made it, never which (owner, 21 Oct 2026). */
  maker?: string;
  /** "Free" when absent. */
  price?: string;
  /** One line for the card. */
  blurb: string;
  /** A short paragraph for the app's page. */
  about: string;
  works: ConnectionKey[];
  /** Things to ask the agent, shown on the app's page. */
  asks: string[];
}

export interface Agent {
  slug: string;
  name: string;
  /** The agent's colour class (marketing.css .mkt-c-*). */
  tone: string;
  /** A still life in public/marketing (team-*.webp), where there is one. */
  image?: string;
  apps: App[];
}

const MAKER = 'Hospo Apps';

export const AGENTS: Agent[] = [
  {
    slug: 'procurement', name: 'Procurement', tone: 'mkt-c-procurement', image: 'team-procurement',
    apps: [
      {
        slug: 'ordering', name: 'Ordering', maker: MAKER, works: ['loaded'],
        blurb: 'Orders drafted from what you sell and hold, for you to approve.',
        about: 'Procurement looks at what you sell and what you hold, works out what you need, and drafts the purchase order. You check it, change it if you like, and approve it.',
        asks: ['What do we need to order for the weekend?', 'Order 20 cartons of Peroni from Coastal Drinks for Friday.', 'Show me the orders still waiting on a delivery.'],
      },
      {
        slug: 'invoice-receiving', name: 'Invoice Receiving', maker: MAKER, works: ['loaded'],
        blurb: 'Every supplier invoice read, checked against its order and received.',
        about: 'Procurement reads each supplier invoice line by line, checks it against the order and receives it. You only look at the lines that don’t add up, and at month end it reconciles the supplier’s statement.',
        asks: ['Receive this morning’s invoices and show me anything that doesn’t match.', 'Why is the Coastal Drinks statement $84 out?', 'Which suppliers put their prices up this month?'],
      },
      {
        slug: 'stocktakes', name: 'Stocktakes', maker: MAKER, works: ['loaded'],
        blurb: 'Stock on hand, counts by area, and the variance explained.',
        about: 'See what you hold and what it’s worth, and find out where a stocktake went wrong, by area and by item.',
        asks: ['What was the variance on last week’s bar stocktake?', 'What’s our stock on hand worth today?', 'Which items went missing in the kitchen count?'],
      },
      {
        slug: 'supplier-tenders', name: 'Supplier Tenders', maker: MAKER, works: ['loaded'],
        blurb: 'Compare suppliers’ prices for the same items before you switch.',
        about: 'Line up what each supplier charges for the same products, so you can see what you’d save before you move an order.',
        asks: ['Which supplier is cheapest for limes?', 'Compare our dry goods prices across suppliers.', 'What would we save moving dairy to another supplier?'],
      },
    ],
  },
  {
    slug: 'time-attendance', name: 'Time & Attendance', tone: 'mkt-c-time', image: 'team-time',
    apps: [
      {
        slug: 'rosters', name: 'Rosters', maker: MAKER, works: ['loaded'],
        blurb: 'Build, cost and publish the week’s roster.',
        about: 'Drag shifts around or ask Norm to make the change, watch labour against sales as you go, and publish when you’re happy.',
        asks: ['Show me next week’s roster.', 'Move Mia to the Friday close.', 'How does labour compare to sales this week?'],
      },
      {
        slug: 'attendance', name: 'Attendance', maker: MAKER, works: ['loaded'],
        blurb: 'Who turned up against the roster, and what it cost.',
        about: 'Norm checks clock-ins against the roster, so you can see who was late, who didn’t show and what the week really cost.',
        asks: ['Who was late this week?', 'Did anyone not turn up for a rostered shift?', 'What did labour cost yesterday against the roster?'],
      },
    ],
  },
  {
    slug: 'executive-chef', name: 'Executive Chef', tone: 'mkt-c-chef', image: 'team-chef',
    apps: [
      {
        slug: 'recipes-and-costing', name: 'Recipes & Costing', maker: MAKER, works: ['loaded'],
        blurb: 'Write recipes and see what every plate costs.',
        about: 'Describe a dish or upload a recipe and Norm writes it up, costed at today’s prices. When a supplier’s price moves, the plate cost moves with it.',
        asks: ['What does our margherita cost to make?', 'Write up a recipe for the new brunch special.', 'Which dishes got dearer this month?'],
      },
      {
        slug: 'menu-manager', name: 'Menu Manager', maker: MAKER, works: ['loaded'],
        blurb: 'Every menu, item and price, across your venues.',
        about: 'See every menu across your venues, find the items that are missing a recipe, and keep prices in step.',
        asks: ['List the items on the lunch menu.', 'Which menu items have no recipe?', 'Show me the dinner menu at Harbourside Kitchen.'],
      },
      {
        slug: 'menu-engineering', name: 'Menu Engineering', maker: MAKER, works: ['loaded'],
        blurb: 'Which dishes to push, rework or drop.',
        about: 'Norm puts each dish’s sales beside its margin, so you can see your stars, the dishes that need a rethink and the ones to take off.',
        asks: ['Which dishes should we push, rework or drop?', 'What’s the margin on each main?', 'How did the new menu do in its first month?'],
      },
    ],
  },
  {
    slug: 'finance', name: 'Finance', tone: 'mkt-c-finance',
    apps: [
      {
        slug: 'bills', name: 'Bills', maker: MAKER, works: ['xero'],
        blurb: 'Checked supplier invoices become bills, coded to your accounts.',
        about: 'Once an invoice has been checked, Norm creates the bill in Xero, coded to your accounts, so nobody types it in twice.',
        asks: ['Turn yesterday’s invoices into bills in Xero.', 'What bills are unpaid?', 'Which bills are due this week?'],
      },
      {
        slug: 'profit-and-loss', name: 'Profit & Loss', maker: MAKER, works: ['xero'],
        blurb: 'Profit and loss by venue and month, explained in plain words.',
        about: 'Ask how a venue or a month went and Norm reads your profit and loss and explains what moved.',
        asks: ['How was last month’s profit?', 'Why was September down on August?', 'Which venue made the most last quarter?'],
      },
    ],
  },
  {
    slug: 'hr', name: 'HR', tone: 'mkt-c-hr', image: 'team-hr',
    apps: [
      {
        slug: 'hiring', name: 'Hiring', maker: MAKER, works: ['hospo-hr'],
        blurb: 'Job posts, a candidate pipeline and a talent pool.',
        about: 'Post roles, move candidates from applied to hired, keep a pool of the people you’d hire again, and get new starters onboarded.',
        asks: ['Open the bar manager role.', 'Show me this week’s applicants.', 'Who’s in our talent pool for the kitchen?'],
      },
      {
        slug: 'training', name: 'Training', maker: MAKER, works: ['hospo-hr'],
        blurb: 'Programs for each role, a plan for each starter, sign-offs on shift.',
        about: 'Set up training programs for each role, give every new starter a plan, and have their trainer sign them off on shift.',
        asks: ['Open Mia’s training plan.', 'Who hasn’t finished their RSA training?', 'Set up a program for new bartenders.'],
      },
      {
        slug: 'reviews', name: 'Reviews', maker: MAKER, works: ['hospo-hr'],
        blurb: 'Review cycles that run on time.',
        about: 'Run reviews against a capability framework for each role, with reminders when someone is due.',
        asks: ['Which reviews are due this month?', 'Open Tom’s last review.', 'Who hasn’t had a review this year?'],
      },
      {
        slug: 'employee-records', name: 'Employee Records', maker: MAKER, works: ['bamboohr'],
        blurb: 'Your people’s records, kept in step with your HR system.',
        about: 'Look people up, see who started when, and keep an eye on open roles in the HR system you already use.',
        asks: ['Find Tom’s employee record.', 'Who started this month?', 'Show me the open jobs in BambooHR.'],
      },
    ],
  },
  {
    slug: 'functions', name: 'Functions', tone: 'mkt-c-functions',
    apps: [
      {
        slug: 'functions', name: 'Functions', maker: MAKER, works: ['hospo-functions'],
        blurb: 'Enquiries, proposals, events, a client portal and the venue inboxes.',
        about: 'Every function enquiry lands in one place and moves through proposal, deposit and run sheet, with a portal where the client confirms the details themselves.',
        asks: ['Show me this week’s function enquiries.', 'Draft a proposal for Mia’s 30th.', 'Which events are still waiting on a deposit?'],
      },
    ],
  },
  {
    slug: 'reports', name: 'Reports', tone: 'mkt-c-reports', image: 'team-reports',
    apps: [
      {
        slug: 'sales-analysis', name: 'Sales Analysis', maker: MAKER, works: ['loaded'],
        blurb: 'Your best and worst sellers, staff sales and how this week compares.',
        about: 'See what sold and what didn’t, how each person on the floor sold, and how any week compares with the one before, last year or budget.',
        asks: ['What were our top sellers last week?', 'How did sales compare with the same week last year?', 'Who sold the most on Saturday?'],
      },
      {
        slug: 'weekly-report', name: 'Weekly Report', maker: MAKER, works: ['loaded'],
        blurb: 'Last week’s numbers in the team’s inbox on Monday morning.',
        about: 'Pick the numbers that matter and Norm emails them to the team every Monday, so nobody has to pull them together.',
        asks: ['Email me last week’s numbers every Monday.', 'Send the managers a weekly sales summary.', 'Add labour to the Monday report.'],
      },
    ],
  },
];

export const ALL_APPS = AGENTS.flatMap(agent => agent.apps.map(app => ({ app, agent })));

export function findApp(slug: string) {
  return ALL_APPS.find(entry => entry.app.slug === slug);
}
