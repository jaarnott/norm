'use client';

import Link from 'next/link';
import { useBreakpoint } from '../../hooks/useBreakpoint';

const FEATURES = [
  {
    title: 'Conversational AI', icon: '💬',
    desc: 'Talk to Norm like you would a colleague. It understands context, asks clarifying questions, and chains actions across your systems.',
    details: ['Natural language queries and commands', 'Multi-turn conversations with context awareness', 'Visible thinking steps so you see the reasoning', 'Parallel tool calls for faster data gathering', 'Approval gates for write operations'],
  },
  {
    title: 'Roster Management', icon: '📅',
    desc: 'Visual drag-and-drop scheduling that syncs with your venue systems in real time.',
    details: ['Week grid view with staff rows and daily columns', 'Day timeline view with hour-by-hour visualization', 'Drag shifts between staff members', 'Resize shifts to adjust clock-in/out times', 'Multi-venue roster support', 'Real-time sync with LoadedHub'],
  },
  {
    title: 'Procurement & Stock', icon: '📦',
    desc: 'Track inventory, generate purchase orders, and manage suppliers from a single conversation.',
    details: ['Stock level queries across time periods', 'Automated purchase order generation', 'Supplier management and ordering', 'Usage calculation and forecasting', 'Budget-based stock recommendations', 'Multi-line PO editor with approval workflow'],
  },
  {
    title: 'HR & Hiring', icon: '👥',
    desc: 'Manage your hiring pipeline from job posting to onboarding, with AI-assisted candidate screening.',
    details: ['Job board with status tracking', 'Candidate management with application history', 'Resume analysis and scoring', 'Screening questions and rating system', 'Integration with BambooHR', 'Employee onboarding workflows'],
  },
  {
    title: 'Automated Tasks', icon: '⚡',
    desc: 'Schedule recurring workflows that run on autopilot — checks, reports, and alerts without lifting a finger.',
    details: ['Daily, weekly, and monthly schedules', 'Manual trigger for on-demand runs', 'Test mode for dry runs', 'Execution history with status tracking', 'Per-agent task routing', 'Pause and resume workflows'],
  },
  {
    title: 'Reporting & Analytics', icon: '📊',
    desc: 'Generate sales reports, stock analyses, and operational summaries on demand or on schedule.',
    details: ['Sales data across time periods', 'Stock on hand and usage reports', 'Budget vs. actual analysis', 'Cross-venue comparisons', 'Automated report scheduling', 'Markdown-formatted output'],
  },
  {
    title: 'Integrations', icon: '🔗',
    desc: 'Norm connects to the systems you already use, with a flexible connector architecture that grows with you.',
    details: ['LoadedHub — rostering, stock, sales', 'BambooHR — jobs, candidates, employees', 'Bidfood — supplier ordering', 'OAuth2 and API key authentication', 'Custom connector builder', 'Response transforms for clean data'],
  },
  {
    title: 'Multi-Venue Management', icon: '🏢',
    desc: 'Manage multiple locations from a single dashboard with per-venue connectors and unified reporting.',
    details: ['Per-venue connector configuration', 'Cross-venue data queries', 'Venue-scoped task routing', 'Team member access control', 'Unified billing per organization'],
  },
];

export default function FeaturesPage() {
  const { isMobile } = useBreakpoint();
  const sectionStyle = { maxWidth: 900, margin: '0 auto', padding: isMobile ? '0 1rem' : '0 2rem' };

  return (
    <div>
      <section style={{ ...sectionStyle, paddingTop: isMobile ? '2.5rem' : '4rem', paddingBottom: '3rem', textAlign: 'center' }}>
        <h1 style={{ fontSize: isMobile ? '1.8rem' : '2.5rem', fontWeight: 800, marginBottom: '0.75rem', color: '#2d2a26' }}>
          Built for hospitality operations
        </h1>
        <p style={{ color: '#999', fontSize: isMobile ? '1rem' : '1.1rem', maxWidth: 550, margin: '0 auto' }}>
          Every feature designed around how hospitality venues actually work — from shift scheduling to stock orders.
        </p>
      </section>

      <section style={{ ...sectionStyle, paddingBottom: '5rem' }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '2rem' }}>
          {FEATURES.map((f) => (
            <div key={f.title} style={{
              display: 'grid', gridTemplateColumns: isMobile ? '1fr' : '1fr 1fr', gap: isMobile ? '1rem' : '2.5rem', alignItems: 'start',
              backgroundColor: '#fff', borderRadius: 14, padding: isMobile ? '1.25rem' : '2rem', border: '1px solid #e8e4de',
            }}>
              <div>
                <div style={{ fontSize: '2rem', marginBottom: '0.75rem' }}>{f.icon}</div>
                <h2 style={{ fontSize: '1.3rem', fontWeight: 700, marginBottom: '0.75rem', color: '#2d2a26' }}>{f.title}</h2>
                <p style={{ color: '#888', fontSize: '0.9rem', lineHeight: 1.6, margin: 0 }}>{f.desc}</p>
              </div>
              <div>
                <ul style={{ listStyle: 'none', padding: 0, margin: 0 }}>
                  {f.details.map(d => (
                    <li key={d} style={{ fontSize: '0.82rem', color: '#666', padding: '0.35rem 0', display: 'flex', alignItems: 'flex-start', gap: 8 }}>
                      <span style={{ color: '#a08060', flexShrink: 0 }}>&#10003;</span>
                      <span>{d}</span>
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* CTA */}
      <section style={{ ...sectionStyle, paddingBottom: '5rem', textAlign: 'center' }}>
        <h2 style={{ fontSize: isMobile ? '1.4rem' : '1.8rem', fontWeight: 700, marginBottom: '1rem', color: '#2d2a26' }}>
          See it in action
        </h2>
        <p style={{ color: '#999', fontSize: '1rem', marginBottom: '2rem' }}>
          Start your free trial and connect your first venue in minutes.
        </p>
        <Link href="/login" style={{
          backgroundColor: '#a08060', color: '#fff', padding: '0.75rem 2rem',
          borderRadius: 10, fontSize: '1rem', fontWeight: 700, textDecoration: 'none',
        }}>
          Get Started Free
        </Link>
      </section>
    </div>
  );
}
