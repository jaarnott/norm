import Link from 'next/link';

const FEATURES = [
  { title: 'AI-Powered Chat', desc: 'Tell Norm what you need in plain English. It figures out the details, calls the right tools, and gets it done.', icon: '💬' },
  { title: 'Smart Rostering', desc: 'Drag-and-drop shift scheduling synced with your venue systems. Norm builds rosters, fills gaps, and handles changes.', icon: '📅' },
  { title: 'Procurement & Stock', desc: 'Track stock levels, generate purchase orders, and manage supplier relationships — all from one conversation.', icon: '📦' },
  { title: 'Hiring Pipeline', desc: 'Post jobs, screen candidates, track applications. Norm reads resumes and helps you find the right people.', icon: '👥' },
  { title: 'Automated Tasks', desc: 'Set up recurring workflows — daily stock checks, weekly reports, candidate screening — that run on autopilot.', icon: '⚡' },
  { title: 'Multi-Venue', desc: 'Manage multiple locations from one dashboard. Each venue connects to its own systems with unified reporting.', icon: '🏢' },
];

const STEPS = [
  { num: '01', title: 'Connect your systems', desc: 'Link your existing tools — LoadedHub, BambooHR, Bidfood, and more. Norm plugs into your stack.' },
  { num: '02', title: 'Ask Norm anything', desc: 'Type what you need in plain English. Norm understands context, queries your data, and takes action.' },
  { num: '03', title: 'Review and approve', desc: 'Norm drafts orders, rosters, and reports. You review, edit, and approve before anything goes live.' },
];

const PLANS = [
  { name: 'Basic', price: 50, tokens: '1M', desc: 'For small venues getting started' },
  { name: 'Standard', price: 100, tokens: '3M', desc: 'For growing operations', popular: true },
  { name: 'Max', price: 200, tokens: '10M', desc: 'For high-volume multi-venue groups' },
];

const CHAT_MESSAGES = [
  { role: 'user', text: 'How much Peroni did we receive last week?' },
  { role: 'assistant', thinking: 'Checking received stock and stock items...' },
  { role: 'assistant', text: 'Last week at La Zeppa you received:\n\n**Peroni Nastro Azzurro 330ml**\n24 cartons on March 17\n\n**Peroni Nastro Azzurro 50L Keg**\n3 kegs on March 19\n\nTotal: 36 cartons across 2 deliveries.' },
  { role: 'user', text: 'Great, can you order more? We need 20 cartons of the 330ml.' },
  { role: 'assistant', text: "I've prepared a draft order for 20 cartons of Peroni 330ml from Drinks Direct.", actions: true },
];

const sectionStyle = { maxWidth: 1200, margin: '0 auto', padding: '0 2rem' };

export default function LandingPage() {
  return (
    <div>
      {/* Hero */}
      <section style={{ ...sectionStyle, paddingTop: '5rem', paddingBottom: '5rem' }}>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '4rem', alignItems: 'center' }}>
          <div>
            <h1 style={{ fontSize: '3.2rem', fontWeight: 800, lineHeight: 1.1, letterSpacing: '-0.03em', margin: '0 0 1.5rem', color: '#2d2a26' }}>
              The best hospitality manager you&apos;ve ever had. Working 24/7.
            </h1>
            <p style={{ fontSize: '1.15rem', color: '#888', lineHeight: 1.6, margin: '0 0 2rem', maxWidth: 480 }}>
              Norm handles rostering, procurement, stock management, and reporting — so you can focus on your guests.
            </p>
            <div style={{ display: 'flex', gap: '0.75rem' }}>
              <Link href="/login" style={{
                backgroundColor: '#a08060', color: '#fff', padding: '0.75rem 2rem',
                borderRadius: 10, fontSize: '1rem', fontWeight: 700, textDecoration: 'none',
              }}>
                Get Started
              </Link>
              <Link href="/features" style={{
                border: '1px solid #d0c8be', color: '#666', padding: '0.75rem 2rem',
                borderRadius: 10, fontSize: '1rem', fontWeight: 600, textDecoration: 'none',
              }}>
                See Features
              </Link>
            </div>
          </div>

          {/* Chat mockup — dark panel for contrast */}
          <div style={{
            backgroundColor: '#1a1a24', borderRadius: 16, padding: '1.5rem',
            boxShadow: '0 20px 60px rgba(0,0,0,0.15)',
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: '1.25rem' }}>
              <div style={{ width: 8, height: 8, borderRadius: '50%', backgroundColor: '#48bb78' }} />
              <span style={{ fontSize: '0.75rem', fontWeight: 600, color: '#888' }}>NORM</span>
              <span style={{ fontSize: '0.65rem', color: '#555', marginLeft: 'auto' }}>La Zeppa</span>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
              {CHAT_MESSAGES.map((msg, i) => (
                <div key={i}>
                  {msg.thinking && (
                    <div style={{ fontSize: '0.72rem', color: '#c4a882', marginBottom: 4, fontStyle: 'italic' }}>
                      {msg.thinking}
                    </div>
                  )}
                  <div style={{
                    padding: '0.6rem 0.85rem', borderRadius: 10, fontSize: '0.82rem', lineHeight: 1.5,
                    ...(msg.role === 'user'
                      ? { backgroundColor: '#2a2a38', color: '#ddd', marginLeft: '2rem' }
                      : { backgroundColor: '#111118', color: '#bbb', marginRight: '1rem', border: '1px solid #2a2a38' }
                    ),
                  }}>
                    {msg.role === 'user' && <div style={{ fontSize: '0.65rem', color: '#888', marginBottom: 2 }}>YOU</div>}
                    {msg.text && msg.text.split('\n').map((line, j) => (
                      <div key={j} style={{ marginBottom: line === '' ? 6 : 0 }}>
                        {line.startsWith('**') ? <strong style={{ color: '#f0f0f5' }}>{line.replace(/\*\*/g, '')}</strong> : line}
                      </div>
                    ))}
                    {msg.actions && (
                      <div style={{ display: 'flex', gap: 6, marginTop: 8 }}>
                        <span style={{ padding: '4px 12px', borderRadius: 6, fontSize: '0.72rem', fontWeight: 600, backgroundColor: '#c4a882', color: '#111' }}>Approve Order</span>
                        <span style={{ padding: '4px 12px', borderRadius: 6, fontSize: '0.72rem', fontWeight: 600, border: '1px solid #444', color: '#888' }}>Edit</span>
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* Features grid */}
      <section style={{ ...sectionStyle, paddingTop: '4rem', paddingBottom: '4rem' }}>
        <h2 style={{ fontSize: '2rem', fontWeight: 700, textAlign: 'center', marginBottom: '0.5rem', color: '#2d2a26' }}>
          Everything you need to run your venue
        </h2>
        <p style={{ textAlign: 'center', color: '#999', fontSize: '1rem', marginBottom: '3rem' }}>
          One AI assistant that connects to all your systems and handles the operational workload.
        </p>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '1.25rem' }}>
          {FEATURES.map(f => (
            <div key={f.title} style={{
              backgroundColor: '#fff', borderRadius: 12, padding: '1.5rem',
              border: '1px solid #e8e4de',
            }}>
              <div style={{ fontSize: '1.75rem', marginBottom: '0.75rem' }}>{f.icon}</div>
              <h3 style={{ fontSize: '1rem', fontWeight: 600, marginBottom: '0.5rem', color: '#2d2a26' }}>{f.title}</h3>
              <p style={{ color: '#888', fontSize: '0.85rem', lineHeight: 1.6, margin: 0 }}>{f.desc}</p>
            </div>
          ))}
        </div>
      </section>

      {/* How it works */}
      <section style={{ ...sectionStyle, paddingTop: '4rem', paddingBottom: '4rem' }}>
        <h2 style={{ fontSize: '2rem', fontWeight: 700, textAlign: 'center', marginBottom: '3rem', color: '#2d2a26' }}>
          How it works
        </h2>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '2rem' }}>
          {STEPS.map(s => (
            <div key={s.num} style={{ textAlign: 'center' }}>
              <div style={{ fontSize: '2.5rem', fontWeight: 800, color: '#c4a882', opacity: 0.4, marginBottom: '0.5rem' }}>{s.num}</div>
              <h3 style={{ fontSize: '1.1rem', fontWeight: 600, marginBottom: '0.5rem', color: '#2d2a26' }}>{s.title}</h3>
              <p style={{ color: '#999', fontSize: '0.85rem', lineHeight: 1.6, margin: 0 }}>{s.desc}</p>
            </div>
          ))}
        </div>
      </section>

      {/* Pricing preview */}
      <section style={{ ...sectionStyle, paddingTop: '4rem', paddingBottom: '5rem' }}>
        <h2 style={{ fontSize: '2rem', fontWeight: 700, textAlign: 'center', marginBottom: '0.5rem', color: '#2d2a26' }}>
          Simple, transparent pricing
        </h2>
        <p style={{ textAlign: 'center', color: '#999', fontSize: '1rem', marginBottom: '2.5rem' }}>
          Start with what you need. Scale as you grow.
        </p>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '1.25rem', maxWidth: 800, margin: '0 auto' }}>
          {PLANS.map(p => (
            <div key={p.name} style={{
              backgroundColor: '#fff', borderRadius: 12, padding: '1.5rem', textAlign: 'center',
              border: p.popular ? '2px solid #a08060' : '1px solid #e8e4de',
              position: 'relative',
            }}>
              {p.popular && (
                <div style={{
                  position: 'absolute', top: -10, left: '50%', transform: 'translateX(-50%)',
                  backgroundColor: '#a08060', color: '#fff', fontSize: '0.65rem', fontWeight: 700,
                  padding: '2px 10px', borderRadius: 10, textTransform: 'uppercase',
                }}>Popular</div>
              )}
              <div style={{ fontSize: '0.85rem', fontWeight: 600, color: '#555', marginBottom: '0.25rem' }}>{p.name}</div>
              <div style={{ fontSize: '2rem', fontWeight: 800, color: '#2d2a26' }}>
                ${p.price}<span style={{ fontSize: '0.85rem', fontWeight: 400, color: '#999' }}>/mo</span>
              </div>
              <div style={{ fontSize: '0.78rem', color: '#999', marginBottom: '0.75rem' }}>{p.tokens} tokens included</div>
              <p style={{ fontSize: '0.78rem', color: '#aaa', margin: 0 }}>{p.desc}</p>
            </div>
          ))}
        </div>
        <div style={{ textAlign: 'center', marginTop: '2rem' }}>
          <Link href="/pricing" style={{ color: '#a08060', fontSize: '0.9rem', fontWeight: 600, textDecoration: 'none' }}>
            View full pricing details &rarr;
          </Link>
        </div>
      </section>

      {/* CTA */}
      <section style={{ ...sectionStyle, paddingTop: '4rem', paddingBottom: '5rem', textAlign: 'center' }}>
        <h2 style={{ fontSize: '2.2rem', fontWeight: 700, marginBottom: '1rem', color: '#2d2a26' }}>
          Ready to put your operations on autopilot?
        </h2>
        <p style={{ color: '#999', fontSize: '1.05rem', marginBottom: '2rem' }}>
          Join hospitality operators who are saving hours every week with Norm.
        </p>
        <Link href="/login" style={{
          backgroundColor: '#a08060', color: '#fff', padding: '0.85rem 2.5rem',
          borderRadius: 10, fontSize: '1.05rem', fontWeight: 700, textDecoration: 'none',
          display: 'inline-block',
        }}>
          Get Started Free
        </Link>
      </section>
    </div>
  );
}
