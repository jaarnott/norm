import type { Metadata } from 'next';
import Link from 'next/link';
import Photo from '../_components/Photo';
import { SIGNUP_HREF } from '../_components/links';

export const metadata: Metadata = {
  title: { absolute: 'Features — Norm' },
  description:
    'Procurement, Time & Attendance, HR, Executive Chef, Marketing and Reports: an AI team for hospitality that works in the systems you already use.',
};

// Same colour per member as the home page (marketing.css .mkt-c-*).
const MEMBERS: { name: string; image: string; tone: string; dark?: boolean; summary: string; does: string[] }[] = [
  {
    name: 'Procurement', image: 'team-procurement', tone: 'mkt-c-procurement',
    summary: 'Ordering, invoices and stock, from the first purchase order to the month-end statement.',
    does: [
      'Purchase orders drafted from what you sell and hold',
      'Supplier invoices read and checked against the order',
      'Clean invoices received automatically, if you choose',
      'Statements reconciled at month end',
      'Stock on hand, stocktakes and variance',
      'Supplier tenders, to compare prices before you switch',
    ],
  },
  {
    name: 'Time & Attendance', image: 'team-time', tone: 'mkt-c-time',
    summary: 'Rosters that fit the trade, and labour you can see coming.',
    does: [
      'Week and day roster views',
      'Drag and resize shifts, then publish',
      'Labour cost against sales and budget',
      'Who turned up, checked against the roster',
    ],
  },
  {
    name: 'HR', image: 'team-hr', tone: 'mkt-c-hr',
    summary: 'From the first job ad to a fully trained team member.',
    does: [
      'Norm Hiring: openings, candidates and a talent pool',
      'CVs read and summarised',
      'Norm Training: programs, plans and on-shift sign-offs',
      'Employee records through BambooHR',
    ],
  },
  {
    name: 'Executive Chef', image: 'team-chef', tone: 'mkt-c-chef',
    summary: 'Recipes and menus that stay costed and current.',
    does: [
      'Recipes written, costed and kept up to date',
      'A recipe document turned into a recipe card',
      'Menus edited in one place',
      'Menu engineering by popularity and margin',
    ],
  },
  {
    name: 'Marketing', image: 'team-marketing', tone: 'mkt-c-marketing',
    summary: 'Keep the room full without another login.',
    does: [
      'Email campaigns written and scheduled in Brevo',
      'Social posts planned in Metricool',
      'Reach and engagement reported back',
    ],
  },
  {
    name: 'Reports', image: 'team-reports', tone: 'mkt-c-reports', dark: true,
    summary: 'Ask a question, get the number. Included with every plan.',
    does: [
      'Sales, labour, stock and cost of goods',
      'Budgets against actuals, venue by venue',
      'Dashboards and saved charts',
      'Reports emailed on a schedule',
    ],
  },
];

const INCLUDED: [string, string][] = [
  ['Approvals you control', 'Every change previewed. You choose what Norm may do without asking.'],
  ['Scheduled tasks', 'Daily, weekly or monthly. Anything that needs you waits and emails you.'],
  ['A memory for your business', 'Norm remembers how you like things done, so you only say it once.'],
  ['Every venue', 'Each venue connects its own systems. Compare them side by side.'],
  ['Roles and permissions', 'Owners, managers, payroll and team members, or roles of your own.'],
  ['Phone and iPad', 'The full app on the device in your apron.'],
  ['App Builder', 'Describe a tool and Norm builds it on your data.'],
  ['Files and photos', 'Send an invoice PDF or a recipe document and Norm reads it.'],
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
      <section className="mkt-section mkt-section--cream" style={{ paddingTop: 56, paddingBottom: 80 }}>
        <div className="mkt-wrap mkt-stack mkt-stack--lg">
          <h1 className="mkt-h1 mkt-h1--page">One AI team for the whole operation.</h1>
          <p className="mkt-lead">
            Norm works through a team of specialists, each with its own job. Hire the ones you need. They share one memory, one set of permissions, and your approval.
          </p>
        </div>
      </section>

      <section className="mkt-section" style={{ paddingTop: 72 }}>
        <div className="mkt-wrap mkt-stack" style={{ gap: 16 }}>
          {MEMBERS.map(m => (
            <article key={m.name} className={`mkt-member-row ${m.tone}${m.dark ? ' mkt-dark' : ''}`}>
              <div className="mkt-stack" style={{ gap: 10 }}>
                <Photo name={m.image} widths={[480, 800]} ratio={1} alt="" sizes="(max-width: 720px) 100vw, 260px" className="mkt-member-thumb" />
                <h2 className="mkt-h2 mkt-h2--sm">{m.name}</h2>
                <p className="mkt-text">{m.summary}</p>
              </div>
              <ul>
                {m.does.map(item => <li key={item}>{item}</li>)}
              </ul>
            </article>
          ))}
        </div>
      </section>

      <section className="mkt-section mkt-section--cream">
        <div className="mkt-wrap mkt-split" style={{ alignItems: 'start' }}>
          <div className="mkt-stack">
            <h2 className="mkt-h2">Every plan includes</h2>
            <p className="mkt-body">The things that make Norm safe to hand work to.</p>
          </div>
          <dl className="mkt-faq" style={{ margin: 0 }}>
            {INCLUDED.map(([title, body]) => (
              <div key={title} style={{ borderTop: '1px solid var(--mkt-border)', padding: '16px 0' }}>
                <dt style={{ fontWeight: 700, fontSize: 17 }}>{title}</dt>
                <dd style={{ margin: '4px 0 0', color: 'var(--mkt-ink-soft)' }}>{body}</dd>
              </div>
            ))}
          </dl>
        </div>
      </section>

      <section className="mkt-section">
        <div className="mkt-wrap mkt-split" style={{ alignItems: 'start', gap: 40 }}>
          <div className="mkt-stack">
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

      <section className="mkt-section mkt-section--saffron">
        <div className="mkt-wrap mkt-split">
          <h2 className="mkt-h2">See it on your own numbers.</h2>
          <div className="mkt-stack mkt-stack--lg">
            <p className="mkt-lead">Connect a venue and ask Norm about last week. Free for 7 days.</p>
            <div className="mkt-actions">
              <Link href={SIGNUP_HREF} className="mkt-btn mkt-btn--primary">Start your free week</Link>
            </div>
          </div>
        </div>
      </section>
    </>
  );
}
