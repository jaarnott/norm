import type { Metadata } from 'next';
import Link from 'next/link';
import { notFound } from 'next/navigation';
import Photo from '../../_components/Photo';
import { CONNECTIONS, findApp } from '../../_components/apps';
import { ConnectionMark } from '../../_components/AppParts';
import { LOGIN_HREF, SIGNUP_HREF } from '../../_components/links';
import { findPublished } from '../../_components/published';

type Params = { params: Promise<{ slug: string }> };

// Rendered on each visit: an app a venue published is looked up from this
// site's own address, which needs the request, and a page built ahead can't
// switch to that at run time (it failed with a 500 in production).
export const dynamic = 'force-dynamic';

const find = async (slug: string) => findApp(slug) ?? (await findPublished(slug));

export async function generateMetadata({ params }: Params): Promise<Metadata> {
  const found = await find((await params).slug);
  if (!found) return {};
  return {
    title: { absolute: `${found.app.name} — Norm App Marketplace` },
    description: `${found.app.blurb} One of ${found.agent.name}’s apps in Norm.`,
  };
}

export default async function AppPage({ params }: Params) {
  const found = await find((await params).slug);
  if (!found) notFound();
  const { app, agent } = found;
  const siblings = agent.apps.filter(a => a.slug !== app.slug);
  // Built by a venue with App Builder and published. Other venues can't
  // switch it on yet (docs/plans/apps-attached-to-agents.md), so its page
  // shows what it does and how to build your own.
  const byVenue = !app.maker;
  const price = app.price ?? 'Free';

  return (
    <>
      <section className="mkt-section mkt-section--cream" style={{ paddingTop: 32, paddingBottom: 72 }}>
        <div className="mkt-wrap mkt-stack mkt-stack--lg">
          <nav aria-label="You are here" className="mkt-crumbs">
            <Link href="/marketplace">Marketplace</Link>
            <span aria-hidden="true">/</span>
            <Link href={`/marketplace#${agent.slug}`}>{agent.name}</Link>
            <span aria-hidden="true">/</span>
            <span>{app.name}</span>
          </nav>
          <div className="mkt-split" style={{ alignItems: 'center', gap: 48 }}>
            <div className="mkt-stack mkt-stack--lg">
              <div className="mkt-app-hero-title">
                <span aria-hidden="true" className={`mkt-app-tile mkt-app-tile--lg ${agent.tone}`}>{app.name[0]}</span>
                <div className="mkt-stack" style={{ gap: 4 }}>
                  <h1 className="mkt-h1 mkt-h1--page" style={{ fontSize: 'clamp(36px, 3.8vw, 52px)' }}>{app.name}</h1>
                  <p className="mkt-text">
                    {byVenue ? `Made by a venue on Norm · in ${agent.name}’s menu` : `by ${app.maker} · one of ${agent.name}’s apps`}
                  </p>
                </div>
              </div>
              <p className="mkt-lead">{app.about}</p>
              <div className="mkt-actions">
                <Link href={SIGNUP_HREF} className="mkt-btn mkt-btn--primary">Start your free week</Link>
                <Link href={LOGIN_HREF} className="mkt-btn mkt-btn--secondary">Already on Norm? Log in</Link>
              </div>
              <p className="mkt-small">
                {byVenue ? 'Built with App Builder, which comes with Norm. Describe the app you want and it makes yours.' : `Free. Works with the ${agent.name} agent.`}
              </p>
            </div>
            {agent.image ? (
              <Photo
                name={agent.image} widths={[480, 800]} ratio={1} alt=""
                sizes="(max-width: 900px) 100vw, 520px"
                className={`mkt-app-hero-photo ${agent.tone}`}
              />
            ) : (
              <div aria-hidden="true" className={`mkt-app-hero-field ${agent.tone}`}>{app.name[0]}</div>
            )}
          </div>
        </div>
      </section>

      <section className="mkt-section" style={{ paddingTop: 0 }}>
        <div className="mkt-wrap">
          <div className="mkt-facts">
            <div>
              <span>AI agent</span>
              <strong><span aria-hidden="true" className={`mkt-swatch ${agent.tone}`} />{agent.name}</strong>
            </div>
            {app.works.length > 0 && (
              <div>
                <span>Works with</span>
                <strong className="mkt-facts-marks">
                  {app.works.map(w => (
                    <span key={w}><ConnectionMark name={w} size={22} />{CONNECTIONS[w].name}</span>
                  ))}
                </strong>
              </div>
            )}
            <div>
              <span>Price</span>
              <strong>{price}</strong>
            </div>
            <div>
              <span>Changes it makes</span>
              <strong>Only with your approval</strong>
            </div>
          </div>
        </div>
      </section>

      <section className="mkt-section" style={{ paddingTop: 0 }}>
        <div className="mkt-wrap mkt-app-body">
          {app.asks.length > 0 && (
            <div className="mkt-stack mkt-stack--lg" style={{ flex: '999 1 560px' }}>
              <h2 className="mkt-h2 mkt-h2--sm">Ask {agent.name} to…</h2>
              <div className="mkt-asks">
                {app.asks.map(ask => <span key={ask} className="mkt-ask">{ask}</span>)}
              </div>
            </div>
          )}
          {siblings.length > 0 && (
            <aside className="mkt-siblings" aria-label={`More of ${agent.name}’s apps`}>
              <strong>{byVenue ? `${agent.name}’s own apps` : `Comes with the ${agent.name} agent`}</strong>
              <span className="mkt-small">Hire {agent.name} and its apps switch on. Switch any of them off in Settings.</span>
              {siblings.map(s => (
                <Link key={s.slug} href={`/marketplace/${s.slug}`} className="mkt-sibling">
                  <span aria-hidden="true" className={`mkt-app-tile mkt-app-tile--sm ${agent.tone}`}>{s.name[0]}</span>
                  <span>
                    <strong>{s.name}</strong>
                    <span>{s.blurb}</span>
                  </span>
                </Link>
              ))}
            </aside>
          )}
        </div>
      </section>

      <section className="mkt-section mkt-section--cream">
        <div className="mkt-wrap mkt-split">
          <h2 className="mkt-h2 mkt-h2--sm">
            {byVenue ? 'Want one like it? Describe it to App Builder. New? Start with a free week.' : 'Already on Norm? Switch it on in Settings. New? Start with a free week.'}
          </h2>
          <div className="mkt-stack mkt-stack--lg">
            <p className="mkt-body">
              {byVenue ? 'Any venue on Norm can build its own apps with App Builder.' : `${app.name} is free with the ${agent.name} agent.`} Norm is free for 7 days, then from $49 a month, with no charge per venue.
            </p>
            <div className="mkt-actions">
              <Link href={SIGNUP_HREF} className="mkt-btn mkt-btn--primary">Start your free week</Link>
              <Link href="/marketplace" className="mkt-btn mkt-btn--secondary">Back to the Marketplace</Link>
            </div>
          </div>
        </div>
      </section>
    </>
  );
}
