/**
 * Workflow status card — the React port of workflow.html, for playbooks bound
 * to display-block whose outcome is not a draft with its own editor (running,
 * completed, pending approval, or a draft type we have no component for).
 *
 * The markdown subset matches workflow.html's: escape everything first, then
 * re-introduce only known markup (tables, headings, bullets, bold/italic,
 * code). Playbook summaries are agent-written, so they are treated as text,
 * never as HTML.
 */
import type { BlockProps } from './registry';

const LABELS: Record<string, { text: string; color: string }> = {
  draft_created: { text: 'Draft created', color: 'var(--accent)' },
  pending_approval: { text: 'Needs approval', color: 'var(--warn)' },
  needs_input: { text: 'Needs your input', color: 'var(--warn)' },
  completed: { text: 'Completed', color: 'var(--good)' },
  running: { text: 'Still running', color: 'var(--muted)' },
};

function esc(s: unknown): string {
  return String(s == null ? '' : s).replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' })[c] as string);
}

function inline(s: string): string {
  return s
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^*])\*([^*]+)\*/g, '$1<em>$2</em>');
}

function mdToHtml(src: string): string {
  const lines = esc(src).split(/\r?\n/);
  const out: string[] = [];
  let i = 0;
  const cells = (r: string) => r.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim());
  while (i < lines.length) {
    const ln = lines[i];
    if (!ln.trim()) { i++; continue; }
    if (/^\s*\|.*\|\s*$/.test(ln) && i + 1 < lines.length && /^\s*\|[\s:|-]+\|\s*$/.test(lines[i + 1])) {
      const head = cells(ln); i += 2;
      const body: string[][] = [];
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) { body.push(cells(lines[i])); i++; }
      out.push(
        '<table><thead><tr>' + head.map((h) => `<th>${inline(h)}</th>`).join('') +
        '</tr></thead><tbody>' +
        body.map((r) =>
          '<tr>' + r.map((c) => {
            const n = /^[-+$]?[\d,.]+%?$/.test(c.replace(/\s/g, ''));
            return `<td class="${n ? 'num' : ''}">${inline(c)}</td>`;
          }).join('') + '</tr>').join('') +
        '</tbody></table>',
      );
      continue;
    }
    const h = ln.match(/^\s*(#{1,3})\s+(.*)$/);
    if (h) { out.push(`<h3>${inline(h[2])}</h3>`); i++; continue; }
    if (/^\s*[-*]\s+/.test(ln)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
        items.push(`<li>${inline(lines[i].replace(/^\s*[-*]\s+/, ''))}</li>`); i++;
      }
      out.push(`<ul>${items.join('')}</ul>`);
      continue;
    }
    const para: string[] = [];
    while (i < lines.length && lines[i].trim() && !/^\s*[-*#]\s/.test(lines[i]) && !/^\s*\|/.test(lines[i])) {
      para.push(lines[i]); i++;
    }
    out.push(`<p>${inline(para.join(' '))}</p>`);
  }
  return out.join('');
}

export default function WorkflowResult({ data }: BlockProps) {
  const d = (data || {}) as {
    status?: string; doc_type?: string; summary?: string; note?: string;
    open_in_norm?: string; error?: string;
  };

  if (d.error) {
    return <p className="err">{String(d.error)}</p>;
  }

  const meta = LABELS[d.status || ''] || { text: d.status || 'Done', color: 'var(--muted)' };
  const summary = d.summary
    ? <div className="md" style={{ fontSize: '.84rem', lineHeight: 1.5, overflowX: 'auto' }}
        dangerouslySetInnerHTML={{ __html: mdToHtml(d.summary) }} />
    : <p style={{ color: 'var(--muted)', fontSize: '.82rem' }}>
        {d.status === 'running' ? 'Norm is still working on this.' : 'No summary was returned.'}
      </p>;

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, margin: '2px 0 10px' }}>
        <span className="badge" style={{ background: 'transparent', color: meta.color, border: `1px solid ${meta.color}` }}>
          {meta.text}
        </span>
        {d.doc_type && (
          <span className="muted" style={{ fontSize: '.78rem' }}>
            {String(d.doc_type).replace(/_/g, ' ')}
          </span>
        )}
      </div>
      {d.note && (
        <p style={{ color: 'var(--muted)', fontSize: '.78rem', margin: '0 0 12px', paddingLeft: 10, borderLeft: '2px solid var(--line)' }}>
          {d.note}
        </p>
      )}
      {summary}
      {d.open_in_norm && (
        <div style={{ marginTop: 14 }}>
          <button
            className="btn"
            onClick={() => window.NormApp.openLink(String(d.open_in_norm))}
          >
            {d.status === 'draft_created' ? 'Review draft in Norm'
              : d.status === 'pending_approval' ? 'Approve in Norm' : 'Open in Norm'}
          </button>
        </div>
      )}
    </div>
  );
}
