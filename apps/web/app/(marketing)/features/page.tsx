import type { Metadata } from 'next';
import Link from 'next/link';
import type { LucideIcon } from 'lucide-react';
import { CalendarClock, ChartColumn, Check, ChefHat, Megaphone, ShoppingCart, Users } from 'lucide-react';
import { SIGNUP_HREF } from '../_components/links';

export const metadata: Metadata = {
  title: { absolute: 'Features — Norm' },
  description:
    'Procurement, Time & Attendance, HR, Executive Chef, Marketing and Reports: an AI team for hospitality that works in the systems you already use.',
};

const MEMBERS: { name: string; icon: LucideIcon; summary: string; does: string[] }[] = [
  {
    name: 'Procurement', icon: ShoppingCart,
    summary: 'Ordering, invoices and stock, from the first purchase order to the month-end statement.',
    does: [
      'Purchase orders drafted from what you sell and hold',
      'Supplier invoices read and checked against the order',
      'Clean invoices received automatically, if you choose',
      'Statements reconciled at month end',
      'Stock on hand, stocktakes and variance',
      'Supplier tenders: compare prices before you switch',
    ],
  },
  {
    name: 'Time & Attendance', icon: CalendarClock,
    summary: 'Rosters that fit the trade, and labour you can see coming.',
    does: [
      'Week and day roster views',
      'Drag and resize shifts; publish when ready',
      'Labour cost against sales and budget',
      'Who turned up, checked against the roster',
    ],
  },
  {
    name: 'HR', icon: Users,
    summary: 'From the first job ad to a fully trained team member.',
    does: [
      'Norm Hiring: openings, candidates and a talent pool',
      'CVs read and summarised',
      'Norm Training: programs, plans and on-shift sign-offs',
      'Employee records through BambooHR',
    ],
  },
  {
    name: 'Executive Chef', icon: ChefHat,
    summary: 'Recipes and menus that stay costed and current.',
    does: [
      'Recipes written, costed and kept up to date',
      'Turn a recipe document into a recipe card',
      'Menus edited in one place',
      'Menu engineering by popularity and margin',
    ],
  },
  {
    name: 'Marketing', icon: Megaphone,
    summary: 'Keep the room full without another login.',
    does: [
      'Email campaigns written and scheduled in Brevo',
      'Social posts planned in Metricool',
      'Reach and engagement reported back',
    ],
  },
  {
    name: 'Reports', icon: ChartColumn,
    summary: 'Ask a question, get the number. Included with every plan.',
    does: [
      'Sales, labour, stock and cost of goods',
      'Budgets against actuals, venue by venue',
      'Dashboards and saved charts',
      'Reports emailed on a schedule',
    ],
  },
];

const INCLUDED: { title: string; body: string }[] = [
  { title: 'Approvals you control', body: 'Every change previewed. Choose what Norm may do without asking.' },
  { title: 'Scheduled tasks', body: 'Daily, weekly or monthly. Work that needs you waits and emails you.' },
  { title: 'A memory for your business', body: 'Norm remembers how you like things done, so you only say it once.' },
  { title: 'Every venue', body: 'Each venue connects its own systems. Compare them side by side.' },
  { title: 'Roles and permissions', body: 'Owners, managers, payroll and team members, or roles of your own.' },
  { title: 'Phone and iPad', body: 'The full app on the device in your apron.' },
  { title: 'App Builder', body: 'Describe a tool and Norm builds it on your data.' },
  { title: 'Files and photos', body: 'Send an invoice PDF or a recipe document and Norm reads it.' },
];

const INTEGRATIONS: [string, string][] = [
  ['LoadedHub', 'Rosters, sales, stock, invoices, recipes'],
  ['Bidfood', 'Supplier ordering'],
  ['BambooHR', 'Employees, jobs and candidates'],
  ['Brevo', 'Email campaigns'],
  ['Metricool', 'Social posts and reach'],
];

export default function FeaturesPage() {
  return (
    <>
      <section className="mkt-section mkt-section--cream" style={{ paddingTop: 48, paddingBottom: 72 }}>
        <div className="mkt-wrap mkt-stack">
          <span className="mkt-eyebrow">Features</span>
          <h1 className="mkt-h1 mkt-h1--page">One AI team for the whole operation.</h1>
          <p className="mkt-lead">
            Norm works through a team of specialists, each with its own job. Hire the ones you need. They share one memory, one set of permissions, and your approval.
          </p>
        </div>
      </section>

      <section className="mkt-section" style={{ paddingTop: 72 }}>
        <div className="mkt-wrap mkt-stack" style={{ gap: 20 }}>
          {MEMBERS.map(({ name, icon: Icon, summary, does }) => (
            <article key={name} className="mkt-member">
              <div className="mkt-stack" style={{ gap: 12 }}>
                <span className="mkt-tile" aria-hidden="true"><Icon size={24} strokeWidth={1.75} /></span>
                <h2 className="mkt-h3 mkt-h3--lg">{name}</h2>
                <p className="mkt-text" style={{ fontSize: 17 }}>{summary}</p>
              </div>
              <ul className="mkt-checks mkt-checks--grid">
                {does.map(item => (
                  <li key={item}><Check size={16} strokeWidth={2.25} aria-hidden="true" />{item}</li>
                ))}
              </ul>
            </article>
          ))}
        </div>
      </section>

      <section className="mkt-section mkt-section--cream">
        <div className="mkt-wrap mkt-stack" style={{ gap: 36 }}>
          <div className="mkt-head">
            <h2 className="mkt-h2">Every plan includes</h2>
            <p className="mkt-body">The things that make Norm safe to hand work to.</p>
          </div>
          <div className="mkt-cards" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 260px), 1fr))', gap: 16 }}>
            {INCLUDED.map(item => (
              <div key={item.title} className="mkt-card mkt-card--quiet">
                <strong style={{ fontSize: 17 }}>{item.title}</strong>
                <span className="mkt-text" style={{ fontSize: 15 }}>{item.body}</span>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="mkt-section">
        <div className="mkt-wrap mkt-split" style={{ alignItems: 'start', gap: 40 }}>
          <div className="mkt-stack" style={{ gap: 12 }}>
            <h2 className="mkt-h2">Works with the systems you already run</h2>
            <p className="mkt-body">Norm reads from and writes to your existing tools, so nothing has to move.</p>
          </div>
          <div className="mkt-list">
            {INTEGRATIONS.map(([name, what]) => (
              <div key={name} className="mkt-list-row">
                <strong>{name}</strong>
                <span style={{ color: 'var(--mkt-ink-soft)', textAlign: 'right' }}>{what}</span>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="mkt-section mkt-section--tint">
        <div className="mkt-wrap mkt-wrap--narrow mkt-stack mkt-stack--lg mkt-center">
          <h2 className="mkt-h2 mkt-h2--xl">See it on your own numbers.</h2>
          <p className="mkt-lead">Connect a venue and ask Norm about last week. Free for 7 days.</p>
          <div className="mkt-actions">
            <Link href={SIGNUP_HREF} className="mkt-btn mkt-btn--primary">Start your free week</Link>
          </div>
        </div>
      </section>
    </>
  );
}
