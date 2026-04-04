'use client';

import { useState, useEffect } from 'react';
import { apiFetch } from '../../../lib/api';
import { BarChart3, Users, ShoppingCart, Loader2 } from 'lucide-react';

interface Template {
  slug: string;
  agent_slug: string;
  title: string;
  description: string;
  chart_count: number;
}

interface TemplateGalleryProps {
  agentSlug: string;
  onInstantiated: () => void;
}

const AGENT_ICONS: Record<string, React.ReactNode> = {
  reports: <BarChart3 size={20} strokeWidth={1.5} />,
  hr: <Users size={20} strokeWidth={1.5} />,
  procurement: <ShoppingCart size={20} strokeWidth={1.5} />,
};

const AGENT_COLORS: Record<string, string> = {
  reports: '#4f8a5e',
  hr: '#5b8abd',
  procurement: '#b07d4f',
};

export default function TemplateGallery({ agentSlug, onInstantiated }: TemplateGalleryProps) {
  const [templates, setTemplates] = useState<Template[]>([]);
  const [loading, setLoading] = useState(true);
  const [instantiating, setInstantiating] = useState<string | null>(null);

  useEffect(() => {
    apiFetch('/api/reports/templates')
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        if (d?.templates) {
          // Filter to templates matching this agent, or show all if none match
          const matching = d.templates.filter((t: Template) => t.agent_slug === agentSlug);
          setTemplates(matching.length > 0 ? matching : d.templates);
        }
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [agentSlug]);

  const handleInstantiate = async (slug: string) => {
    setInstantiating(slug);
    try {
      const res = await apiFetch(`/api/reports/templates/${slug}/instantiate`, { method: 'POST' });
      if (res.ok) {
        onInstantiated();
      }
    } catch { /* ignore */ }
    setInstantiating(null);
  };

  if (loading) {
    return (
      <div style={{ padding: '3rem', textAlign: 'center', color: '#999' }}>
        <Loader2 size={24} style={{ animation: 'spin 1s linear infinite' }} />
      </div>
    );
  }

  if (templates.length === 0) {
    return (
      <div style={{ padding: '2rem', textAlign: 'center', color: '#999' }}>
        <div style={{ fontSize: '1rem', marginBottom: '0.5rem' }}>No dashboard configured</div>
        <div style={{ fontSize: '0.82rem' }}>Ask Norm to build a dashboard for you, or create one from the Reports page.</div>
      </div>
    );
  }

  return (
    <div style={{ padding: '1.5rem' }}>
      <div style={{ textAlign: 'center', marginBottom: '1.5rem' }}>
        <h2 style={{ margin: 0, fontSize: '1.1rem', fontWeight: 700, color: '#1a1a1a' }}>
          Get started with a template
        </h2>
        <p style={{ margin: '0.35rem 0 0', fontSize: '0.8rem', color: '#999' }}>
          Choose a template to create your dashboard, then customise it to fit your needs.
        </p>
      </div>

      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))',
        gap: '1rem',
        maxWidth: 900,
        margin: '0 auto',
      }}>
        {templates.map(t => {
          const color = AGENT_COLORS[t.agent_slug] || '#888';
          const isInstantiating = instantiating === t.slug;

          return (
            <div
              key={t.slug}
              style={{
                border: '1px solid #f0ebe5',
                borderRadius: 12,
                padding: '1.25rem',
                backgroundColor: '#fff',
                display: 'flex',
                flexDirection: 'column',
                gap: '0.75rem',
                transition: 'box-shadow 0.15s',
                cursor: 'default',
              }}
              onMouseEnter={e => (e.currentTarget.style.boxShadow = '0 2px 12px rgba(0,0,0,0.06)')}
              onMouseLeave={e => (e.currentTarget.style.boxShadow = 'none')}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                <div style={{
                  width: 36, height: 36, borderRadius: 8,
                  backgroundColor: `${color}14`,
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  color,
                }}>
                  {AGENT_ICONS[t.agent_slug] || <BarChart3 size={20} strokeWidth={1.5} />}
                </div>
                <div>
                  <div style={{ fontSize: '0.9rem', fontWeight: 600, color: '#333' }}>{t.title}</div>
                  <div style={{ fontSize: '0.68rem', color: '#bbb' }}>
                    {t.chart_count} chart{t.chart_count !== 1 ? 's' : ''}
                  </div>
                </div>
              </div>

              <p style={{ margin: 0, fontSize: '0.78rem', color: '#777', lineHeight: 1.45 }}>
                {t.description}
              </p>

              <button
                onClick={() => handleInstantiate(t.slug)}
                disabled={!!instantiating}
                style={{
                  marginTop: 'auto',
                  padding: '8px 16px',
                  fontSize: '0.78rem',
                  fontWeight: 600,
                  fontFamily: 'inherit',
                  border: 'none',
                  borderRadius: 8,
                  backgroundColor: color,
                  color: '#fff',
                  cursor: instantiating ? 'not-allowed' : 'pointer',
                  opacity: instantiating && !isInstantiating ? 0.5 : 1,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  gap: 6,
                }}
              >
                {isInstantiating ? (
                  <>
                    <Loader2 size={14} style={{ animation: 'spin 1s linear infinite' }} />
                    Creating...
                  </>
                ) : (
                  'Use Template'
                )}
              </button>
            </div>
          );
        })}
      </div>

      <style>{`@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }`}</style>
    </div>
  );
}
