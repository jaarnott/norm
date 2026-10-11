import type { Metadata } from 'next';
import Link from 'next/link';
import type { LucideIcon } from 'lucide-react';
import {
  ArrowRight, Blocks, Building2, CalendarClock, ChartColumn, Check, ChefHat, GraduationCap,
  Mail, Megaphone, Receipt, ShieldCheck, ShoppingCart, UserRoundSearch, Users,
} from 'lucide-react';
import { SIGNUP_HREF } from './_components/links';

export const metadata: Metadata = {
  title: { absolute: 'Norm — the AI operations team for hospitality' },
  description:
    'Norm handles rostering, procurement, stock management, and reporting — so you can focus on your guests. Free for 7 days.',
};

const TEAM: { name: string; icon: LucideIcon; job: string; does: string[] }[] = [
  {
    name: 'Procurement', icon: ShoppingCart, job: 'Orders, invoices and stock.',
    does: ['Drafts purchase orders for you to approve', 'Receives supplier invoices against the order', 'Reconciles statements, stocktakes and tenders'],
  },
  {
    name: 'Time & Attendance', icon: CalendarClock, job: 'Rosters and labour.',
    does: ['Edits and publishes rosters', 'Watches labour cost against sales', 'Checks who turned up against the roster'],
  },
  {
    name: 'HR', icon: Users, job: 'Hiring, training and your people.',
    does: ['Job posts and a candidate pipeline', 'Training programs with on-shift sign-offs', 'Employee records through BambooHR'],
  },
  {
    name: 'Executive Chef', icon: ChefHat, job: 'Recipes, menus and margins.',
    does: ['Writes and costs recipes', 'Keeps every menu up to date', 'Menu engineering: what to push, rework or drop'],
  },
  {
    name: 'Marketing', icon: Megaphone, job: 'Campaigns and socials.',
    does: ['Writes and schedules email campaigns', 'Plans and queues social posts', 'Reports back on reach'],
  },
  {
    name: 'Reports', icon: ChartColumn, job: 'The numbers, when you need them.',
    does: ['Sales, labour, stock and cost of goods', 'Dashboards and saved charts', 'Weekly reports in your inbox, on schedule'],
  },
];

// The Cook Brothers venues Norm runs at every day. Only names confirmed for
// public use go here.
const VENUES = ['La Zeppa', 'Freeman & Grey', 'The Glass Goose', 'Mr Murdochs', 'Dunedin Social Club'];

// Real quotes only, each with the person's OK to use their name and words.
// The section stays hidden until there is one.
const QUOTES: { quote: string; name: string; role: string }[] = [];

function Checks({ items, large = false }: { items: string[]; large?: boolean }) {
  return (
    <ul className={large ? 'mkt-checks mkt-checks--lg' : 'mkt-checks'}>
      {items.map(item => (
        <li key={item}>
          <Check size={large ? 20 : 16} strokeWidth={2.25} aria-hidden="true" />
          {item}
        </li>
      ))}
    </ul>
  );
}

function Tile({ icon: Icon }: { icon: LucideIcon }) {
  return (
    <span className="mkt-tile" aria-hidden="true">
      <Icon size={24} strokeWidth={1.75} />
    </span>
  );
}

export default function HomePage() {
  return (
    <>
      {/* Hero */}
      <section className="mkt-section mkt-section--cream" style={{ paddingTop: 40 }}>
        <div className="mkt-wrap mkt-split">
          <div className="mkt-stack mkt-stack--lg">
            <span className="mkt-pill">Your AI operations team for hospitality</span>
            <h1 className="mkt-h1">
              The best hospitality manager you&apos;ve ever had. <span className="mkt-mark">Working 24/7.</span>
            </h1>
            <p className="mkt-lead">
              Norm handles rostering, procurement, stock management, and reporting — so you can focus on your guests.
            </p>
            <div className="mkt-actions">
              <Link href={SIGNUP_HREF} className="mkt-btn mkt-btn--primary">
                Start your free week
                <ArrowRight size={18} strokeWidth={2} aria-hidden="true" />
              </Link>
              <Link href="/features" className="mkt-btn mkt-btn--secondary">See what Norm does</Link>
            </div>
            <p className="mkt-small">Free for 7 days. Then from $50 a month, plus $10 per venue.</p>
          </div>

          {/* A Norm conversation, drawn the way the app draws it. */}
          <div
            className="mkt-demo"
            role="img"
            aria-label="A Norm conversation: a manager asks how much Peroni arrived last week, Norm answers with a table, then prepares an order that waits for approval."
          >
            <div className="mkt-demo-bar">
              <span className="mkt-demo-title"><span className="mkt-demo-mark">N</span>Procurement</span>
              <span className="mkt-demo-venue"><Building2 size={14} strokeWidth={2} aria-hidden="true" />Harbourside Kitchen</span>
            </div>
            <div className="mkt-demo-body">
              <div className="mkt-bubble mkt-step">How much Peroni did we get last week?</div>
              <div className="mkt-reply mkt-step">
                <span>Harbourside received <strong>36 cartons</strong> of Peroni 330ml across two deliveries:</span>
                <div className="mkt-table">
                  <div className="mkt-table-row mkt-table-row--head"><span>Delivered</span><span>Supplier</span><span>Cartons</span></div>
                  <div className="mkt-table-row"><span>Tuesday</span><span>Coastal Drinks</span><span className="mkt-num">24</span></div>
                  <div className="mkt-table-row"><span>Friday</span><span>Coastal Drinks</span><span className="mkt-num">12</span></div>
                </div>
              </div>
              <div className="mkt-bubble mkt-step">Order 20 more for Friday.</div>
              <div className="mkt-approval mkt-step">
                <div className="mkt-approval-head">
                  <span className="mkt-badge">Approval needed</span>
                  Place an order with Coastal Drinks
                </div>
                <div className="mkt-approval-body">
                  <div className="mkt-approval-line"><span>Peroni Nastro Azzurro 330ml — 20 cartons</span><strong className="mkt-num">$1,170.00</strong></div>
                  <span className="mkt-small" style={{ fontSize: 13 }}>Delivery Friday · nothing is sent until you approve</span>
                </div>
                <div className="mkt-approval-actions">
                  <span className="mkt-fake-btn">Decline</span>
                  <span className="mkt-fake-btn mkt-fake-btn--primary">Approve order</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Where it runs */}
      <section className="mkt-proof" aria-label="Where Norm runs">
        <div className="mkt-wrap mkt-proof-inner">
          <span className="mkt-proof-label">Built with Cook Brothers Bars and run every day across their venues</span>
          <ul className="mkt-proof-names">
            {VENUES.map(v => <li key={v}>{v}</li>)}
          </ul>
        </div>
      </section>

      {/* Meet your AI team */}
      <section className="mkt-section">
        <div className="mkt-wrap mkt-stack mkt-stack--xl">
          <div className="mkt-head">
            <span className="mkt-eyebrow">Meet your AI team</span>
            <h2 className="mkt-h2">Hire the team members you need. They never clock off.</h2>
            <p className="mkt-body">Each one knows its job, works in the systems you already use, and checks with you before it changes anything.</p>
          </div>
          <div className="mkt-cards">
            {TEAM.map(member => (
              <div key={member.name} className="mkt-card">
                <Tile icon={member.icon} />
                <h3 className="mkt-h3">{member.name}</h3>
                <p className="mkt-text">{member.job}</p>
                <Checks items={member.does} />
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* You stay in charge */}
      <section className="mkt-section mkt-section--cream">
        <div className="mkt-wrap mkt-split">
          <div className="mkt-stack mkt-stack--lg">
            <span className="mkt-eyebrow">You stay in charge</span>
            <h2 className="mkt-h2">Norm asks before it changes anything.</h2>
            <p className="mkt-body">
              Every order, roster change or stock update comes as a card showing exactly what will happen. Approve it, edit it or say no. When you trust Norm with something, tell it once.
            </p>
            <ul className="mkt-checks mkt-checks--lg">
              <li><ShieldCheck size={20} strokeWidth={2} aria-hidden="true" />Every change previewed before it happens</li>
              <li><Check size={20} strokeWidth={2} aria-hidden="true" />Choose what Norm may do without asking — just for you</li>
              <li><Mail size={20} strokeWidth={2} aria-hidden="true" />Overnight work that needs you waits, and emails you</li>
            </ul>
          </div>
          <div
            className="mkt-panel mkt-stack"
            role="img"
            aria-label="Norm's approval settings for supplier invoices, with Receive clean invoices selected."
            style={{ padding: 24, gap: 12, borderRadius: 20, boxShadow: '0 24px 60px rgba(70, 48, 16, 0.10)' }}
          >
            <strong style={{ fontSize: 17 }}>Receive supplier invoices</strong>
            <div className="mkt-choice">
              <span className="mkt-radio" aria-hidden="true" />
              <div><strong>Ask me</strong><span>Norm checks each invoice and shows it to you first.</span></div>
            </div>
            <div className="mkt-choice mkt-choice--on">
              <span className="mkt-radio" aria-hidden="true" />
              <div><strong>Receive clean invoices</strong><span>Invoices that match the order exactly go straight through. Anything else waits for you.</span></div>
            </div>
            <div className="mkt-choice">
              <span className="mkt-radio" aria-hidden="true" />
              <div><strong>Autopilot</strong><span>Norm fixes what it can and receives the rest, within the limits you set.</span></div>
            </div>
          </div>
        </div>
      </section>

      {/* Invoices */}
      <section className="mkt-section mkt-section--tight-bottom">
        <div className="mkt-wrap mkt-split">
          <div className="mkt-frame" role="img" aria-label="A supplier invoice checked against its order: one line matched, one price rise and one item not on the order flagged.">
            <div className="mkt-panel">
              <div className="mkt-panel-row" style={{ padding: '14px 16px' }}>
                <span style={{ display: 'flex', flexDirection: 'column' }}>
                  <strong style={{ fontSize: 15 }}>Coastal Drinks · Invoice 20431</strong>
                  <span className="mkt-small" style={{ fontSize: 13 }}>Against order PO-1182</span>
                </span>
                <span className="mkt-badge mkt-badge--warn">2 lines need you</span>
              </div>
              <div className="mkt-panel-row"><span>Peroni 330ml × 20 cartons</span><span className="mkt-badge mkt-badge--ok">Matched</span></div>
              <div className="mkt-panel-row"><span>Corona 355ml × 10 cartons</span><span className="mkt-badge mkt-badge--warn">Price up 4%</span></div>
              <div className="mkt-panel-row"><span>Limes 3kg × 2</span><span className="mkt-badge mkt-badge--warn">Not on the order</span></div>
            </div>
          </div>
          <div className="mkt-stack">
            <Tile icon={Receipt} />
            <h2 className="mkt-h2 mkt-h2--sm">Invoices in. Matched. Reconciled.</h2>
            <p className="mkt-body">
              Norm reads each supplier invoice, checks it line by line against what you ordered, and receives it into your stock system. You only look at what doesn&apos;t add up. At month end it reconciles the statement too.
            </p>
          </div>
        </div>
      </section>

      {/* Rosters */}
      <section className="mkt-section mkt-section--tight-top mkt-section--tight-bottom">
        <div className="mkt-wrap mkt-split">
          <div className="mkt-stack">
            <Tile icon={CalendarClock} />
            <h2 className="mkt-h2 mkt-h2--sm">Rosters that keep an eye on the numbers.</h2>
            <p className="mkt-body">
              Open the roster and drag shifts around, or just ask Norm to make the change. Publish when you&apos;re happy, and see labour against sales as you go.
            </p>
          </div>
          <div className="mkt-frame" role="img" aria-label="A week's roster for four staff, with labour at 27 percent of forecast sales against a 30 percent target.">
            <div className="mkt-panel mkt-roster">
              <div className="mkt-roster-row mkt-roster-row--head"><span>Staff</span><span>Mon</span><span>Tue</span><span>Wed</span><span>Thu</span><span>Fri</span></div>
              <div className="mkt-roster-row"><span>Mia R.</span><span className="mkt-shift">10–6</span><span className="mkt-shift">10–6</span><span /><span className="mkt-shift">4–12</span><span className="mkt-shift">4–12</span></div>
              <div className="mkt-roster-row"><span>Tom E.</span><span /><span className="mkt-shift mkt-shift--alt">7–3</span><span className="mkt-shift mkt-shift--alt">7–3</span><span className="mkt-shift mkt-shift--alt">7–3</span><span /></div>
              <div className="mkt-roster-row"><span>Priya S.</span><span className="mkt-shift">4–11</span><span /><span className="mkt-shift">4–11</span><span /><span className="mkt-shift">5–1</span></div>
              <div className="mkt-roster-row"><span>Josh K.</span><span className="mkt-shift mkt-shift--alt">11–7</span><span className="mkt-shift mkt-shift--alt">11–7</span><span className="mkt-shift mkt-shift--alt">11–7</span><span /><span /></div>
              <div className="mkt-panel-row" style={{ background: '#fbf9f6' }}>
                <span>Labour <strong>27%</strong> of forecast sales</span>
                <span className="mkt-badge mkt-badge--ok">Under 30% target</span>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Reports */}
      <section className="mkt-section mkt-section--tight-top">
        <div className="mkt-wrap mkt-split">
          <div className="mkt-frame mkt-stack" style={{ gap: 14 }} role="img" aria-label="A weekly report: sales of $48,210, up 6 percent; labour 27.4 percent and cost of goods 29.1 percent of sales; a bar for each day.">
            <div className="mkt-kpis">
              <div className="mkt-kpi"><div className="mkt-kpi-label">Sales</div><div className="mkt-kpi-value">$48,210</div><div className="mkt-kpi-note mkt-kpi-note--up">+6% on last week</div></div>
              <div className="mkt-kpi"><div className="mkt-kpi-label">Labour</div><div className="mkt-kpi-value">27.4%</div><div className="mkt-kpi-note">of sales</div></div>
              <div className="mkt-kpi"><div className="mkt-kpi-label">Cost of goods</div><div className="mkt-kpi-value">29.1%</div><div className="mkt-kpi-note">of sales</div></div>
            </div>
            <div className="mkt-panel" style={{ padding: '16px 16px 10px', borderRadius: 12 }}>
              <div className="mkt-bars">
                {[46, 40, 52, 63, 92, 100, 58].map((h, i) => <span key={i} style={{ height: `${h}%` }} />)}
              </div>
              <div className="mkt-bar-labels">
                {['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map(d => <span key={d}>{d}</span>)}
              </div>
            </div>
            <span className="mkt-small" style={{ display: 'flex', alignItems: 'center', gap: 8, color: 'var(--mkt-ink-soft)' }}>
              <Mail size={16} strokeWidth={2} aria-hidden="true" />Emailed to the team every Monday at 7am
            </span>
          </div>
          <div className="mkt-stack">
            <Tile icon={ChartColumn} />
            <h2 className="mkt-h2 mkt-h2--sm">Reports that turn up on time.</h2>
            <p className="mkt-body">
              Ask any question about sales, labour or stock and get the answer as a table or chart. Save the ones you check every week, and Norm sends them before the first coffee.
            </p>
          </div>
        </div>
      </section>

      {/* Built for the floor */}
      <section className="mkt-section mkt-section--cream">
        <div className="mkt-wrap mkt-split">
          <div className="mkt-stack mkt-stack--lg">
            <span className="mkt-eyebrow">Built for the floor</span>
            <h2 className="mkt-h2">Run every venue from your pocket.</h2>
            <p className="mkt-body">
              Norm works on your phone and iPad as well as it does at the desk. Every venue connects its own systems, and everyone sees only what their role allows.
            </p>
            <div className="mkt-cards mkt-cards--sm">
              <div className="mkt-card mkt-card--quiet"><strong>Phone and iPad</strong><span className="mkt-text" style={{ fontSize: 15 }}>The whole app, sized for a busy shift.</span></div>
              <div className="mkt-card mkt-card--quiet"><strong>Every venue</strong><span className="mkt-text" style={{ fontSize: 15 }}>One place for the group, each venue with its own systems.</span></div>
              <div className="mkt-card mkt-card--quiet"><strong>Roles and permissions</strong><span className="mkt-text" style={{ fontSize: 15 }}>Owners, managers and team members each see their part.</span></div>
              <div className="mkt-card mkt-card--quiet"><strong>Works with your systems</strong><span className="mkt-text" style={{ fontSize: 15 }}>LoadedHub, Bidfood, BambooHR, Brevo and Metricool.</span></div>
            </div>
          </div>
          <div className="mkt-phone" role="img" aria-label="Norm on a phone: team members along the top and recent conversations below.">
            <div className="mkt-phone-screen">
              <div className="mkt-phone-head"><span className="mkt-demo-mark">N</span>Norm</div>
              <div className="mkt-phone-members">
                <span className="mkt-phone-member mkt-phone-member--on"><ShoppingCart size={22} strokeWidth={1.75} aria-hidden="true" />Orders</span>
                <span className="mkt-phone-member"><CalendarClock size={22} strokeWidth={1.75} aria-hidden="true" />Roster</span>
                <span className="mkt-phone-member"><ChefHat size={22} strokeWidth={1.75} aria-hidden="true" />Kitchen</span>
                <span className="mkt-phone-member"><ChartColumn size={22} strokeWidth={1.75} aria-hidden="true" />Reports</span>
              </div>
              <div className="mkt-phone-list">
                <div><span>Friday&apos;s Coastal Drinks order</span><span className="mkt-badge mkt-badge--warn" style={{ fontSize: 11 }}>Approval needed</span></div>
                <div><span>Invoices from this morning</span><span className="mkt-when">8:42</span></div>
                <div><span>Stocktake variance, bar</span><span className="mkt-when">Yesterday</span></div>
                <div><span>Last week&apos;s sales</span><span className="mkt-when">Mon</span></div>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Make it yours */}
      <section className="mkt-section">
        <div className="mkt-wrap mkt-stack" style={{ gap: 40 }}>
          <div className="mkt-head">
            <span className="mkt-eyebrow">Make it yours</span>
            <h2 className="mkt-h2">Need something that isn&apos;t there? Describe it.</h2>
          </div>
          <div className="mkt-cards">
            <div className="mkt-card mkt-card--ink">
              <Tile icon={Blocks} />
              <h3 className="mkt-h3">App Builder</h3>
              <p className="mkt-text">Tell Norm the tool you wish you had — a prep list, a waste tracker, a weekly venue scorecard. It builds it with your real data and your permissions, and you can share it with the team.</p>
            </div>
            <div className="mkt-card">
              <Tile icon={UserRoundSearch} />
              <h3 className="mkt-h3">Norm Hiring</h3>
              <p className="mkt-text">Openings, candidates and a talent pool in one board. Norm reads the CVs so you can shortlist faster.</p>
            </div>
            <div className="mkt-card">
              <Tile icon={GraduationCap} />
              <h3 className="mkt-h3">Norm Training</h3>
              <p className="mkt-text">Training programs, a plan for each new starter, and sign-offs done on shift.</p>
            </div>
          </div>
        </div>
      </section>

      {QUOTES.length > 0 && (
        <section className="mkt-section mkt-section--cream">
          <div className="mkt-wrap mkt-cards" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 420px), 1fr))' }}>
            {QUOTES.map(q => (
              <figure key={q.name} className="mkt-quote">
                <blockquote>“{q.quote}”</blockquote>
                <figcaption><strong>{q.name}</strong>{q.role}</figcaption>
              </figure>
            ))}
          </div>
        </section>
      )}

      {/* Closing call to action */}
      <section className="mkt-section mkt-section--tint">
        <div className="mkt-wrap mkt-wrap--narrow mkt-stack mkt-stack--lg mkt-center">
          <h2 className="mkt-h2 mkt-h2--xl">Put Norm on the team. Free for a week.</h2>
          <p className="mkt-lead">
            Then from $50 a month plus $10 per venue. Every plan includes every feature — plans differ only in how much Norm can do each month.
          </p>
          <div className="mkt-actions">
            <Link href={SIGNUP_HREF} className="mkt-btn mkt-btn--primary">Start your free week</Link>
            <Link href="/pricing" className="mkt-btn mkt-btn--secondary">See pricing</Link>
          </div>
        </div>
      </section>
    </>
  );
}
