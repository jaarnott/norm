'use client';

import { useState, useEffect } from 'react';

const DOMAIN_ICONS: Record<string, string> = {
  procurement: '\u{1F4E6}',
  hr: '\u{1F464}',
  unknown: '\u{2753}',
};

interface RoutingIndicatorProps {
  isVisible: boolean;
  resolvedDomain?: string | null;
}

export default function RoutingIndicator({ isVisible, resolvedDomain }: RoutingIndicatorProps) {
  const [step, setStep] = useState(0);

  useEffect(() => {
    if (!isVisible) {
      setStep(0);
      return;
    }
    setStep(1);
    const t1 = setTimeout(() => setStep(2), 400);
    const t2 = setTimeout(() => setStep(3), 800);
    return () => { clearTimeout(t1); clearTimeout(t2); };
  }, [isVisible]);

  if (!isVisible) return null;

  const domainLabel = resolvedDomain || 'agent';
  const icon = DOMAIN_ICONS[resolvedDomain || ''] || '\u{1F916}';

  return (
    <div style={{
      padding: '0.75rem 1rem',
      borderBottom: '1px solid #f0ebe5',
      backgroundColor: '#faf8f5',
    }}>
      <div style={{
        display: 'flex',
        flexDirection: 'column',
        gap: '0.3rem',
        fontSize: '0.78rem',
        color: '#666',
      }}>
        <span style={{ opacity: step >= 1 ? 1 : 0.3, transition: 'opacity 0.3s' }}>
          {'\u{1F9E0}'} Supervisor analysing request...
        </span>
        {step >= 2 && (
          <span style={{ opacity: step >= 2 ? 1 : 0.3, transition: 'opacity 0.3s' }}>
            {'\u{27A1}\u{FE0F}'} Routing to {domainLabel} agent
          </span>
        )}
        {step >= 3 && (
          <span style={{ opacity: step >= 3 ? 1 : 0.3, transition: 'opacity 0.3s' }}>
            {icon} {domainLabel.charAt(0).toUpperCase() + domainLabel.slice(1)} agent preparing task...
          </span>
        )}
      </div>
    </div>
  );
}
