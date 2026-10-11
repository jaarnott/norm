import type { Metadata } from 'next';
import Link from 'next/link';
import { ArrowRight, Building2, CalendarClock, ChartColumn, ChefHat, ShoppingCart } from 'lucide-react';
import Photo from './_components/Photo';
import { SIGNUP_HREF } from './_components/links';

export const metadata: Metadata = {
  title: { absolute: 'Norm — the AI operations team for hospitality' },
  description:
    'Norm handles rostering, procurement, stock management, and reporting — so you can focus on your guests. Free for 7 days.',
};

// Each team member keeps one colour across the site (marketing.css .mkt-c-*).
// `image` is a still life on the member's colour (public/marketing/team-*.webp).
const TEAM: { name: string; image: string; tone: string; dark?: boolean; job: string; does: string[] }[] = [
  {
    name: 'Procurement', image: 'team-procurement', tone: 'mkt-c-procurement', job: 'Orders, supplier invoices and stock.',
    does: ['Drafts purchase orders for you to approve', 'Checks each invoice against the order', 'Reconciles statements, stocktakes and tenders'],
  },
  {
    name: 'Time & Attendance', image: 'team-time', tone: 'mkt-c-time', job: 'Rosters and the cost of them.',
    does: ['Edits and publishes rosters', 'Watches labour against sales', 'Checks who turned up against the roster'],
  },
  {
    name: 'HR', image: 'team-hr', tone: 'mkt-c-hr', job: 'Hiring, training and your people.',
    does: ['Job posts and a candidate pipeline', 'Training with on-shift sign-offs', 'Employee records through BambooHR'],
  },
  {
    name: 'Executive Chef', image: 'team-chef', tone: 'mkt-c-chef', job: 'Recipes, menus and margins.',
    does: ['Writes and costs recipes', 'Keeps every menu up to date', 'Shows which dishes to push, rework or drop'],
  },
  {
    name: 'Marketing', image: 'team-marketing', tone: 'mkt-c-marketing', job: 'Campaigns and socials.',
    does: ['Writes and schedules email campaigns', 'Plans and queues social posts', 'Reports back on reach'],
  },
  {
    name: 'Reports', image: 'team-reports', tone: 'mkt-c-reports', dark: true, job: 'The numbers, whenever you ask.',
    does: ['Sales, labour, stock and cost of goods', 'Dashboards and saved charts', 'A weekly report in your inbox'],
  },
];

// Venues using Norm, each named on its own (city from its address in Norm).
const VENUES: [string, string][] = [
  ['La Zeppa', 'Auckland'],
  ['Freeman & Grey', 'Auckland'],
  ['The Glass Goose', 'Auckland'],
  ['Mr Murdochs', 'Auckland'],
  ['Dunedin Social Club', 'Dunedin'],
  ['Bessie', 'Christchurch'],
];

// Real quotes only, each with the person's OK to use their name and words.
// The section stays hidden until there is one.
const QUOTES: { quote: string; name: string; role: string }[] = [];

export default function HomePage() {
  return (
    <>
      {/* Hero */}
      <section className="mkt-section mkt-section--cream mkt-hero">
        <div className="mkt-wrap mkt-hero-grid">
          <div className="mkt-stack mkt-stack--lg">
            <h1 className="mkt-h1">The best hospitality manager you&apos;ve ever had. Working 24/7.</h1>
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

          <div className="mkt-hero-visual">
            {/* Norm, as a person: the hospitality manager the product stands in
                for. A generated portrait (no real person or venue), so no caption.
                Persona and source brief: docs/norm-persona.md. */}
            <Photo
              name="norm-pub" widths={[900, 1536]} ratio={1024 / 1536}
              alt="Norm, a friendly hospitality manager in a green work shirt, leaning on the bar of a classic pub with a coffee and grinning at you."
              sizes="(max-width: 900px) 100vw, 60vw"
              className="mkt-hero-photo"
              eager
            />
            {/* A Norm conversation, drawn the way the app draws it. */}
            <div
              className="mkt-demo"
              role="img"
              aria-label="Norm conversation: a manager asks how much Peroni arrived last week, Norm answers 36 cartons, then prepares an order that waits for approval."
            >
              <div className="mkt-demo-bar">
                <span className="mkt-demo-title"><span className="mkt-demo-mark">N</span>Procurement</span>
                <span className="mkt-demo-venue"><Building2 size={13} strokeWidth={2} aria-hidden="true" />Harbourside Kitchen</span>
              </div>
              <div className="mkt-demo-body">
                <div className="mkt-bubble mkt-step">How much Peroni came in last week? Order 20 more for Friday.</div>
                <div className="mkt-step">36 cartons across two deliveries from Coastal Drinks. I&apos;ve drafted Friday&apos;s order:</div>
                <div className="mkt-approval mkt-step">
                  <div className="mkt-approval-head">
                    <span className="mkt-badge">Approval needed</span>
                    Order from Coastal Drinks
                  </div>
                  <div className="mkt-approval-body">
                    <span>Peroni 330ml × 20 cartons</span>
                    <strong className="mkt-num">$1,170.00</strong>
                  </div>
                  <div className="mkt-approval-actions">
                    <span className="mkt-fake-btn">Decline</span>
                    <span className="mkt-fake-btn mkt-fake-btn--primary">Approve order</span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* The AI team */}
      <section className="mkt-section">
        <div className="mkt-wrap mkt-stack mkt-stack--xl">
          <div className="mkt-head">
            <h2 className="mkt-h2">A team member for each part of the business.</h2>
            <p className="mkt-body">
              Hire the ones you need. Each works in the systems you already use, and checks with you before it changes anything.
            </p>
          </div>
          <div className="mkt-team">
            {TEAM.map(member => (
              <div key={member.name} className={`mkt-member-card ${member.tone}${member.dark ? ' mkt-dark' : ''}`}>
                <Photo name={member.image} widths={[480, 800]} ratio={1} alt="" sizes="(max-width: 720px) 100vw, 380px" className="mkt-member-media" />
                <h3 className="mkt-h3">{member.name}</h3>
                <p className="mkt-text">{member.job}</p>
                <ul>
                  {member.does.map(item => <li key={item}>{item}</li>)}
                </ul>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Approvals */}
      <section className="mkt-section mkt-section--leaf">
        <div className="mkt-wrap mkt-split">
          <div className="mkt-stack mkt-stack--lg">
            <h2 className="mkt-h2">Norm asks before it changes anything.</h2>
            <p className="mkt-body">
              Every order, roster change or stock update arrives as a card showing exactly what will happen. Approve it, edit it, or say no.
            </p>
            <p className="mkt-body">
              When you trust Norm with something, tell it once. That choice is yours alone, and any overnight job that still needs you waits and sends you an email.
            </p>
          </div>
          <div className="mkt-choices" role="img" aria-label="Norm's approval settings for supplier invoices, with Receive clean invoices selected.">
            <strong style={{ fontSize: 17, marginBottom: 4 }}>Receive supplier invoices</strong>
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
      <section className="mkt-section" style={{ paddingBottom: 56 }}>
        <div className="mkt-wrap mkt-split">
          <div className="mkt-field mkt-field--saffron" role="img" aria-label="A supplier invoice checked against its order: one line matched, one price rise and one item not on the order flagged.">
            <div className="mkt-panel">
              <div className="mkt-panel-row" style={{ padding: '14px 16px' }}>
                <span style={{ display: 'flex', flexDirection: 'column' }}>
                  <strong style={{ fontSize: 15 }}>Coastal Drinks, invoice 20431</strong>
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
            <h2 className="mkt-h2 mkt-h2--sm">Every invoice checked against the order before it&apos;s received.</h2>
            <p className="mkt-body">
              Norm reads each supplier invoice line by line and receives it into your stock system. You only look at the lines that don&apos;t add up. At month end it reconciles the statement as well.
            </p>
          </div>
        </div>
      </section>

      {/* Rosters */}
      <section className="mkt-section" style={{ paddingBlock: 56 }}>
        <div className="mkt-wrap mkt-split">
          <div className="mkt-stack">
            <h2 className="mkt-h2 mkt-h2--sm">Rosters with the labour cost beside them.</h2>
            <p className="mkt-body">
              Drag shifts around, or ask Norm to make the change. Publish when you&apos;re happy, and keep an eye on labour against sales while you work.
            </p>
          </div>
          <div className="mkt-field mkt-field--sky" role="img" aria-label="A week's roster for four staff, with labour at 27 percent of forecast sales against a 30 percent target.">
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
      <section className="mkt-section" style={{ paddingTop: 56 }}>
        <div className="mkt-wrap mkt-split">
          <div className="mkt-field mkt-field--sage mkt-stack" style={{ gap: 12 }} role="img" aria-label="A weekly report: sales of $48,210, up 6 percent; labour 27.4 percent and cost of goods 29.1 percent of sales; a bar for each day.">
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
          </div>
          <div className="mkt-stack">
            <h2 className="mkt-h2 mkt-h2--sm">Last week&apos;s numbers, in your inbox on Monday morning.</h2>
            <p className="mkt-body">
              Ask anything about sales, labour or stock and get a table or a chart back. Save the ones you check every week and Norm emails them to the team.
            </p>
          </div>
        </div>
      </section>

      {/* The venues using Norm, each on its own; real photos from one of them. */}
      <section className="mkt-section mkt-section--cream">
        <div className="mkt-wrap mkt-stack mkt-stack--xl">
          <div className="mkt-split" style={{ alignItems: 'end', gap: 32 }}>
            <div className="mkt-stack">
              <h2 className="mkt-h2">Venues running on Norm.</h2>
              <p className="mkt-body">
                Bars, restaurants and function venues use Norm for the orders, invoices, rosters and numbers behind each service.
              </p>
            </div>
            <ul className="mkt-venues" aria-label="Venues using Norm">
              {VENUES.map(([name, city]) => <li key={name}>{name}<span>{city}</span></li>)}
            </ul>
          </div>
          <div className="mkt-stack" style={{ gap: 10 }}>
            <div className="mkt-gallery">
              <Photo name="glass-goose-deck" widths={[640, 1100]} ratio={1620 / 1100} alt="A full deck at The Glass Goose, under hanging greenery and festoon lights." sizes="(max-width: 720px) 50vw, 290px" />
              <Photo name="glass-goose-cocktail" widths={[520, 950]} ratio={1323 / 936} alt="A yellow cocktail garnished with a leaf, held up against tropical plants." sizes="(max-width: 720px) 50vw, 290px" />
              <Photo name="glass-goose-canapes" widths={[415]} ratio={462 / 415} alt="Canapés on a wooden board: crostini with pickled onion and fried chicken pieces." sizes="(max-width: 720px) 50vw, 290px" />
              <Photo name="glass-goose-party" widths={[520, 830]} ratio={1652 / 830} alt="Two guests laughing at a party under hanging garlands." sizes="(max-width: 720px) 50vw, 290px" />
            </div>
            <p className="mkt-caption">Photos from The Glass Goose, Auckland.</p>
          </div>
        </div>
      </section>

      {/* On the floor */}
      <section className="mkt-section">
        <div className="mkt-wrap mkt-split">
          <div className="mkt-field mkt-field--blush" style={{ display: 'grid', placeItems: 'center' }}>
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
          <div className="mkt-stack mkt-stack--lg">
            <h2 className="mkt-h2">Made for the middle of a shift.</h2>
            <p className="mkt-body">
              Norm works on a phone or an iPad as well as it does at the desk. Every venue connects its own systems, and each person sees only what their role allows.
            </p>
            <p className="mkt-body">
              It works with LoadedHub, Bidfood, BambooHR, Brevo and Metricool, so nothing has to move.
            </p>
          </div>
        </div>
      </section>

      {/* Make it yours */}
      <section className="mkt-section mkt-section--flush-top">
        <div className="mkt-wrap mkt-stack mkt-stack--xl">
          <h2 className="mkt-h2" style={{ maxWidth: 760 }}>Need something that isn&apos;t there? Describe it.</h2>
          <div className="mkt-columns">
            <div>
              <h3 className="mkt-h3">App Builder</h3>
              <p className="mkt-text">Tell Norm about the tool you wish you had, like a prep list, a waste tracker or a weekly venue scorecard. It builds it on your real data, with your permissions, and you can share it with the team.</p>
            </div>
            <div>
              <h3 className="mkt-h3">Norm Hiring</h3>
              <p className="mkt-text">Openings, candidates and a talent pool on one board. Norm reads the CVs so you can shortlist faster.</p>
            </div>
            <div>
              <h3 className="mkt-h3">Norm Training</h3>
              <p className="mkt-text">Training programs, a plan for each new starter, and sign-offs done on shift.</p>
            </div>
          </div>
        </div>
      </section>

      {QUOTES.length > 0 && (
        <section className="mkt-section mkt-section--cream">
          <div className="mkt-wrap mkt-split" style={{ alignItems: 'start' }}>
            {QUOTES.map(q => (
              <figure key={q.name} className="mkt-quote">
                <blockquote>“{q.quote}”</blockquote>
                <figcaption><strong>{q.name}</strong>{q.role}</figcaption>
              </figure>
            ))}
          </div>
        </section>
      )}

      {/* Close */}
      <section className="mkt-section mkt-section--saffron">
        <div className="mkt-wrap mkt-split">
          <h2 className="mkt-h2">Start with a free week.</h2>
          <div className="mkt-stack mkt-stack--lg">
            <p className="mkt-lead">
              Then from $50 a month, plus $10 per venue. Every plan includes every feature; plans differ only in how much Norm can do each month.
            </p>
            <div className="mkt-actions">
              <Link href={SIGNUP_HREF} className="mkt-btn mkt-btn--primary">Start your free week</Link>
              <Link href="/pricing" className="mkt-btn mkt-btn--secondary">See pricing</Link>
            </div>
          </div>
        </div>
      </section>
    </>
  );
}
