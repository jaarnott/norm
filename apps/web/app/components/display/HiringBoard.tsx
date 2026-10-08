'use client';

import { useState, useEffect, useCallback, type CSSProperties, type ReactNode } from 'react';
import { ChevronRight, Download } from 'lucide-react';
import type { DisplayBlockProps } from './DisplayBlockRenderer';
import { apiFetch } from '../../lib/api';
import PageHeader from '../ui/PageHeader';
import PageState from '../ui/PageState';
import Badge, { type BadgeTone } from '../ui/Badge';
import Button from '../ui/Button';
import Icon from '../ui/Icon';

// --- Types (normalised from BambooHR) ---

interface JobSummary {
  id: string;
  title: string;
  department: string | null;
  location: string | null;
  status: string;
  candidate_count: number;
  posted_date: string | null;
  hiring_lead: string | null;
}

interface ApplicationSummary {
  id: string;
  candidate_id: string;
  candidate_name: string;
  candidate_email: string;
  candidate_source: string;
  status: string;
  rating: number | null;
  applied_at: string | null;
  job_title: string | null;
}

interface ApplicationDetail {
  id: string;
  candidate_name: string;
  candidate_email: string;
  candidate_phone: string | null;
  candidate_source: string;
  status: string;
  rating: number | null;
  applied_at: string | null;
  job_title: string | null;
  hiring_lead: string | null;
  desired_salary: string | null;
  linkedin_url: string | null;
  website_url: string | null;
  education: string | null;
  available_start_date: string | null;
  questions_and_answers: { question: string; answer: string }[];
  has_resume: boolean;
  resume_file_id: string | null;
  comment_count: number;
}

type ViewMode = 'jobs' | 'job_detail' | 'candidate_detail';

// --- BambooHR response mapping ---

function extractJobs(data: unknown): JobSummary[] {
  const raw = Array.isArray(data) ? data : [];
  return raw.map((j: Record<string, unknown>) => ({
    id: String(j.id || ''),
    title: (j.title as Record<string, unknown>)?.label as string || String(j.title || ''),
    department: (j.department as Record<string, unknown>)?.label as string || null,
    location: (j.location as Record<string, unknown>)?.label as string || null,
    status: ((j.status as Record<string, unknown>)?.label as string || 'Unknown').toLowerCase(),
    candidate_count: Number(j.totalApplicantsCount || j.activeApplicantsCount || 0),
    posted_date: j.postedDate as string || null,
    hiring_lead: j.hiringLead ? `${(j.hiringLead as Record<string, unknown>).firstName || ''} ${(j.hiringLead as Record<string, unknown>).lastName || ''}`.trim() : null,
  }));
}

function extractApplications(data: unknown): ApplicationSummary[] {
  let raw: unknown[] = [];
  if (Array.isArray(data)) raw = data;
  else if (data && typeof data === 'object' && Array.isArray((data as Record<string, unknown>).applications)) {
    raw = (data as Record<string, unknown>).applications as unknown[];
  }
  return raw.map((_a: unknown) => {
    const a = _a as Record<string, unknown>;
    const applicant = a.applicant as Record<string, unknown> || {};
    const job = a.job as Record<string, unknown> || {};
    const jobTitle = job.title as Record<string, unknown> | string || {};
    return {
      id: String(a.id || ''),
      candidate_id: String(applicant.id || ''),
      candidate_name: `${applicant.firstName || ''} ${applicant.lastName || ''}`.trim(),
      candidate_email: String(applicant.email || ''),
      candidate_source: String(applicant.source || ''),
      status: ((a.status as Record<string, unknown>)?.label as string || 'Unknown'),
      rating: a.rating != null ? Number(a.rating) : null,
      applied_at: a.appliedDate as string || null,
      job_title: typeof jobTitle === 'string' ? jobTitle : (jobTitle as Record<string, unknown>)?.label as string || null,
    };
  });
}

function extractApplicationDetail(data: unknown): ApplicationDetail | null {
  if (!data || typeof data !== 'object') return null;
  const d = data as Record<string, unknown>;
  const applicant = d.applicant as Record<string, unknown> || {};
  const job = d.job as Record<string, unknown> || {};
  const jobTitle = job.title as Record<string, unknown> | string || {};
  const status = d.status as Record<string, unknown> || {};
  const hiringLead = (job.hiringLead as Record<string, unknown>) || null;
  const qna = Array.isArray(d.questionsAndAnswers) ? d.questionsAndAnswers : [];

  return {
    id: String(d.id || ''),
    candidate_name: `${applicant.firstName || ''} ${applicant.lastName || ''}`.trim(),
    candidate_email: String(applicant.email || ''),
    candidate_phone: (applicant.phoneNumber as string) || null,
    candidate_source: String(applicant.source || ''),
    status: (status.label as string) || 'Unknown',
    rating: d.rating != null ? Number(d.rating) : null,
    applied_at: (d.appliedDate as string) || null,
    job_title: typeof jobTitle === 'string' ? jobTitle : (jobTitle as Record<string, unknown>)?.label as string || null,
    hiring_lead: hiringLead ? `${hiringLead.firstName || ''} ${hiringLead.lastName || ''}`.trim() : null,
    desired_salary: (d.desiredSalary as string) || null,
    linkedin_url: (applicant.linkedinUrl as string) || null,
    website_url: (applicant.websiteUrl as string) || null,
    education: (applicant.education as string) || null,
    available_start_date: (applicant.availableStartDate as string) || null,
    questions_and_answers: qna.map((q: Record<string, unknown>) => ({
      question: ((q.question as Record<string, unknown>)?.label as string) || '',
      answer: ((q.answer as Record<string, unknown>)?.label as string) || '',
    })),
    has_resume: !!d.resumeFileId,
    resume_file_id: d.resumeFileId ? String(d.resumeFileId) : null,
    comment_count: Number(d.commentCount || 0),
  };
}

// --- UI Helpers ---

/** BambooHR's labels come in Title Case ("Schedule Phone Screen"); Norm shows sentence case. */
function sentenceCase(s: string): string {
  return s ? s.charAt(0).toUpperCase() + s.slice(1).toLowerCase() : s;
}

function plural(n: number, word: string): string {
  return `${n} ${word}${n !== 1 ? 's' : ''}`;
}

/** A posting is live (open), a draft, or paused (on hold); closed and filled need no colour. */
const JOB_STATUS_TONES: Record<string, BadgeTone> = { open: 'ok', draft: 'info', 'on hold': 'warn' };

function JobStatusBadge({ status }: { status: string }) {
  return <Badge tone={JOB_STATUS_TONES[status.toLowerCase()] ?? 'neutral'}>{sentenceCase(status)}</Badge>;
}

/** A candidate's stage says where they are in the pipeline, not how it went: always neutral. */
function StageBadge({ status }: { status: string }) {
  return <Badge>{sentenceCase(status)}</Badge>;
}

const META: CSSProperties = { fontSize: 'var(--fs-sm)', color: 'var(--muted)' };
const SUBLINE: CSSProperties = { fontSize: 'var(--fs-xs)', color: 'var(--muted)' };
const LINK: CSSProperties = { color: 'var(--accent)', textDecoration: 'underline', textUnderlineOffset: 2 };

/** A table row's name is a real button with no handler of its own: Tab reaches
 *  it and Enter clicks it, and the click bubbles to the row, which opens it. */
const ROW_NAME: CSSProperties = {
  padding: 0, border: 'none', background: 'none', fontFamily: 'inherit', fontSize: 'inherit',
  lineHeight: 'inherit', fontWeight: 600, color: 'var(--text)', textAlign: 'left', cursor: 'pointer',
};

/** Below this width the lists become rows of cards: phones, and narrow chat
 *  panes or dashboard tiles on a wide screen. */
const TABLE_MIN_WIDTH = 640;

/** The status filter: the same chips as the thread filters. */
function chipStyle(on: boolean): CSSProperties {
  return {
    flex: '0 0 auto',
    whiteSpace: 'nowrap',
    padding: '4px 8px',
    fontSize: 'var(--fs-xs)',
    fontWeight: on ? 600 : 500,
    color: on ? 'var(--text)' : 'var(--text-soft)',
    backgroundColor: on ? 'var(--selected)' : 'transparent',
    border: `1px solid ${on ? 'var(--brand-soft)' : 'var(--line)'}`,
    borderRadius: 999,
    cursor: 'pointer',
  };
}

/** Narrow widths get rows instead of a table: a white card each on the page,
 *  flat divided rows inside a chat card. */
function cardRowStyle(card: boolean): CSSProperties {
  return {
    display: 'block', width: '100%', boxSizing: 'border-box',
    fontFamily: 'inherit', fontSize: 'inherit', lineHeight: 1.45, fontVariantNumeric: 'tabular-nums',
    color: 'var(--text)', textAlign: 'left', cursor: 'pointer',
    borderStyle: 'solid', borderColor: 'var(--line)',
    ...(card
      ? { padding: '14px 16px', borderWidth: 1, borderRadius: 'var(--radius-lg)', background: 'var(--bg)' }
      : { padding: '12px 16px', borderWidth: '1px 0 0', borderRadius: 0, background: 'none' }),
  };
}

const CARD_LIST: CSSProperties = { listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column' };

/** First line of a card row: the name, and its badge on the right. */
function CardRowTop({ name, badge }: { name: string; badge: ReactNode }) {
  return (
    <span style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12 }}>
      <span style={{ minWidth: 0, overflowWrap: 'anywhere', fontSize: 'var(--fs-base)', fontWeight: 600 }}>{name}</span>
      {badge}
    </span>
  );
}

/** The page is the frame: on a page the content sits straight on the cream.
 *  In a conversation (or a dashboard tile) it is one compact white card. */
function Frame({ page, children }: { page: boolean; children: ReactNode }) {
  if (page) return <>{children}</>;
  return <div className="n-card" style={{ overflow: 'hidden', lineHeight: 1.45 }}>{children}</div>;
}

/** THE page header on a page; a small title row at the top of a chat card. */
function Head({ page, title, status, meta, actions, children, titleOnPhone }: {
  page: boolean;
  /** A job or a candidate: the title says more than the menu label. */
  titleOnPhone?: boolean;
  title: ReactNode;
  status?: ReactNode;
  meta?: ReactNode;
  actions?: ReactNode;
  children?: ReactNode;
}) {
  if (page) return <PageHeader title={title} status={status} meta={meta} actions={actions} titleOnPhone={titleOnPhone}>{children}</PageHeader>;
  return (
    <div style={{ padding: '14px 16px 12px' }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <h2 style={{ margin: 0, fontSize: 'var(--fs-md)', fontWeight: 600, lineHeight: 1.3, color: 'var(--text)' }}>{title}</h2>
            {status}
          </div>
          {meta && <div style={{ marginTop: 2, ...META }}>{meta}</div>}
        </div>
        {actions && <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>{actions}</div>}
      </div>
      {children && <div style={{ marginTop: 10 }}>{children}</div>}
    </div>
  );
}

/** A titled block of the candidate view: its own card on a page, a divided
 *  section inside the chat card. */
function Section({ page, title, children }: { page: boolean; title: string; children: ReactNode }) {
  const H = page ? 'h2' : 'h3';
  return (
    <section
      className={page ? 'n-card' : undefined}
      style={page ? { marginBottom: 12, padding: '14px 16px 8px' } : { padding: '12px 16px 8px', borderTop: '1px solid var(--line)' }}
    >
      <H style={{ margin: '0 0 4px', fontSize: 'var(--fs-md)', fontWeight: 600, lineHeight: 1.3, color: 'var(--text)' }}>{title}</H>
      {children}
    </section>
  );
}

/** A label and its value side by side, or stacked (phones, long questions). */
function Field({ label, stacked, first, children }: { label: ReactNode; stacked: boolean; first: boolean; children: ReactNode }) {
  return (
    <div style={{
      display: 'flex', flexDirection: stacked ? 'column' : 'row', gap: stacked ? 2 : 16,
      padding: '8px 0', borderTop: first ? 'none' : '1px solid var(--line-soft)',
    }}>
      <div style={{ flex: stacked ? 'none' : '0 0 180px', fontSize: 'var(--fs-sm)', fontWeight: 500, color: 'var(--text-soft)' }}>{label}</div>
      <div style={{ flex: 1, minWidth: 0, overflowWrap: 'anywhere', fontSize: 'var(--fs-base)', color: 'var(--text)' }}>{children}</div>
    </div>
  );
}

function timeAgo(dateStr: string | null): string {
  if (!dateStr) return '';
  const d = new Date(dateStr);
  const now = new Date();
  const diffDays = Math.floor((now.getTime() - d.getTime()) / 86400000);
  if (diffDays === 0) return 'Today';
  if (diffDays === 1) return 'Yesterday';
  if (diffDays < 7) return `${diffDays}d ago`;
  if (diffDays < 30) return `${Math.floor(diffDays / 7)}w ago`;
  return d.toLocaleDateString('en-NZ', { day: 'numeric', month: 'short' });
}

async function callConnector(connector: string, action: string, params: Record<string, unknown> = {}) {
  const res = await apiFetch(`/api/connectors/${connector}/execute/${action}`, {
    method: 'POST',
    body: JSON.stringify({ params }),
  });
  if (!res.ok) throw new Error(`Failed: ${res.status}`);
  const result = await res.json();
  if (result.success === false) throw new Error(result.error || 'Failed');
  return result.data ?? result;
}

// --- Component ---

export default function HiringBoard({ data, props }: DisplayBlockProps) {
  const connector = (props?.connector_name as string) || 'bamboohr';
  const initialJobId = (props?.initial_job_id as string) || null;
  // A PAGE instance (FunctionalPage marks it with persistVenue) gets the page
  // header and sits straight on the cream; in a conversation it stays one
  // compact card.
  const isPage = !!props?.persistVenue;

  // The board's own width decides table or cards — not the viewport, since a
  // split conversation or a dashboard tile can make it narrow on a wide screen.
  const [width, setWidth] = useState(0);
  const measure = useCallback((el: HTMLDivElement | null) => {
    if (!el) return;
    setWidth(el.offsetWidth);
    const ro = new ResizeObserver(() => setWidth(el.offsetWidth));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const narrow = width < TABLE_MIN_WIDTH;

  const [view, setView] = useState<ViewMode>(initialJobId ? 'job_detail' : 'jobs');
  const [selectedJobId, setSelectedJobId] = useState<string | null>(initialJobId);
  const [selectedJobTitle, setSelectedJobTitle] = useState<string>('');

  const [jobs, setJobs] = useState<JobSummary[]>(() => extractJobs(data));
  const [jobFilter, setJobFilter] = useState<string>('all');
  const [applications, setApplications] = useState<ApplicationSummary[]>([]);
  const [selectedApp, setSelectedApp] = useState<ApplicationSummary | null>(null);
  const [appDetail, setAppDetail] = useState<ApplicationDetail | null>(null);
  const [loading, setLoading] = useState(false);
  // Why the last load failed, so a failure reads as an error — never as
  // "No positions found."
  const [loadError, setLoadError] = useState<string | null>(null);

  // Load jobs on mount if not pre-populated
  const loadJobs = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const result = await callConnector(connector, 'get_jobs');
      setJobs(extractJobs(result));
    } catch (e) { setLoadError(e instanceof Error ? e.message : String(e)); }
    finally { setLoading(false); }
  }, [connector]);

  useEffect(() => {
    if (jobs.length === 0) loadJobs();
  }, []);

  // Load applications for a job
  const loadApplications = useCallback(async (jobId: string) => {
    setLoading(true);
    setLoadError(null);
    try {
      const result = await callConnector(connector, 'get_applications', { job_id: jobId });
      setApplications(extractApplications(result));
    } catch (e) { setLoadError(e instanceof Error ? e.message : String(e)); }
    finally { setLoading(false); }
  }, [connector]);

  // Load full application detail
  const loadApplicationDetail = useCallback(async (applicationId: string) => {
    setLoading(true);
    setLoadError(null);
    try {
      const result = await callConnector(connector, 'get_application_details', { application_id: applicationId });
      setAppDetail(extractApplicationDetail(result));
    } catch (e) { setLoadError(e instanceof Error ? e.message : String(e)); }
    finally { setLoading(false); }
  }, [connector]);

  // Navigation
  const goToJob = useCallback((job: JobSummary) => {
    setSelectedJobId(job.id);
    setSelectedJobTitle(job.title);
    setView('job_detail');
    loadApplications(job.id);
  }, [loadApplications]);

  const goToCandidate = useCallback((app: ApplicationSummary) => {
    setSelectedApp(app);
    setAppDetail(null);
    setView('candidate_detail');
    loadApplicationDetail(app.id);
  }, [loadApplicationDetail]);

  const goBackToJobs = useCallback(() => {
    setView('jobs');
    setSelectedJobId(null);
    setSelectedJobTitle('');
    setApplications([]);
    setSelectedApp(null);
    loadJobs();
  }, [loadJobs]);

  const goBackToJob = useCallback(() => {
    setView('job_detail');
    setSelectedApp(null);
    setAppDetail(null);
    if (selectedJobId) loadApplications(selectedJobId);
  }, [loadApplications, selectedJobId]);

  // Inside a chat card a table's outer cells line up with the card's 16px padding.
  const edgeL: CSSProperties | undefined = isPage ? undefined : { paddingLeft: 16 };
  const edgeR: CSSProperties | undefined = isPage ? undefined : { paddingRight: 16 };
  const rowChevron = <Icon icon={ChevronRight} size="dense" tone="muted" style={{ display: 'block' }} />;

  const errorState = (title: string) => (loadError && !loading ? (
    <div style={isPage ? { marginBottom: 12 } : { padding: '0 16px 14px' }}>
      <PageState kind="error" title={title} detail={loadError} />
    </div>
  ) : null);

  // --- Breadcrumb: Jobs › Head Chef › Jane Doe. Each level above the current one leads back. ---
  const jobTitle = selectedJobTitle || jobs.find(j => j.id === selectedJobId)?.title || '';
  const crumbSep = <Icon icon={ChevronRight} size="dense" tone="muted" />;
  const breadcrumb = (
    <nav aria-label="Breadcrumb" style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 4, marginBottom: isPage ? 6 : 8 }}>
      <button type="button" className="n-back" onClick={goBackToJobs}>Jobs</button>
      {view !== 'jobs' && (
        <>
          {crumbSep}
          <button
            type="button"
            className="n-back"
            onClick={goBackToJob}
            aria-current={view === 'job_detail' ? 'page' : undefined}
            style={view === 'job_detail' ? { color: 'var(--text)' } : undefined}
          >
            {jobTitle}
          </button>
        </>
      )}
      {view === 'candidate_detail' && selectedApp && (
        <>
          {crumbSep}
          <span aria-current="page" style={{ fontSize: 'var(--fs-sm)', fontWeight: 500, color: 'var(--text)' }}>{selectedApp.candidate_name}</span>
        </>
      )}
    </nav>
  );

  // --- Jobs View ---
  if (view === 'jobs') {
    const filteredJobs = jobFilter === 'all' ? jobs : jobs.filter(j => j.status === jobFilter);
    const statuses = ['all', ...Array.from(new Set(jobs.map(j => j.status)))];

    const filters = jobs.length > 0 ? (
      <div role="group" aria-label="Filter positions by status" style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
        {statuses.map(f => (
          <button key={f} type="button" aria-pressed={jobFilter === f} onClick={() => setJobFilter(f)} style={chipStyle(jobFilter === f)}>
            {f === 'all' ? 'All' : sentenceCase(f)}
          </button>
        ))}
      </div>
    ) : null;

    let list: ReactNode = null;
    if (filteredJobs.length > 0) {
      list = narrow ? (
        <ul aria-label="Positions" style={{ ...CARD_LIST, gap: isPage ? 10 : 0 }}>
          {filteredJobs.map(job => {
            const where = [job.department, job.location].filter(Boolean).join(' · ');
            const facts = [
              plural(job.candidate_count, 'applicant'),
              timeAgo(job.posted_date),
              job.hiring_lead && `Lead: ${job.hiring_lead}`,
            ].filter(Boolean).join(' · ');
            return (
              <li key={job.id}>
                <button type="button" onClick={() => goToJob(job)} style={cardRowStyle(isPage)}>
                  <CardRowTop name={job.title} badge={<JobStatusBadge status={job.status} />} />
                  {where && <span style={{ display: 'block', marginTop: 2, ...META }}>{where}</span>}
                  <span style={{ display: 'block', marginTop: 2, ...META }}>{facts}</span>
                </button>
              </li>
            );
          })}
        </ul>
      ) : (
        <table className="n-table">
          <thead>
            <tr>
              <th style={edgeL}>Position</th>
              <th>Hiring lead</th>
              <th>Status</th>
              <th className="num">Applicants</th>
              <th className="num">Posted</th>
              <th style={edgeR} />
            </tr>
          </thead>
          <tbody>
            {filteredJobs.map(job => {
              const where = [job.department, job.location].filter(Boolean).join(' · ');
              return (
                <tr key={job.id} onClick={() => goToJob(job)} style={{ cursor: 'pointer' }}>
                  <td style={edgeL}>
                    <button type="button" style={ROW_NAME}>{job.title}</button>
                    {where && <div style={SUBLINE}>{where}</div>}
                  </td>
                  <td style={{ color: 'var(--text-soft)' }}>{job.hiring_lead}</td>
                  <td><JobStatusBadge status={job.status} /></td>
                  <td className="num">{job.candidate_count}</td>
                  <td className="num" style={{ color: 'var(--muted)', whiteSpace: 'nowrap' }}>{timeAgo(job.posted_date)}</td>
                  <td style={{ width: 16, ...edgeR }}>{rowChevron}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      );
    }

    return (
      <div ref={measure}>
        <Frame page={isPage}>
          <Head page={isPage} title="Open Positions" meta={jobs.length > 0 ? plural(jobs.length, 'job') : undefined}>
            {filters}
          </Head>
          {errorState('Couldn’t load positions')}
          {loading && jobs.length === 0 && <PageState kind="loading" title="Loading positions…" />}
          {!loading && !loadError && filteredJobs.length === 0 && (
            <PageState kind="empty" title={jobs.length === 0 ? 'No positions found.' : 'No positions match this filter.'} />
          )}
          {list}
        </Frame>
      </div>
    );
  }

  // --- Job Detail View (Candidates) ---
  if (view === 'job_detail') {
    const job = jobs.find(j => j.id === selectedJobId);
    const meta = [
      job ? [job.department, job.location].filter(Boolean).join(' · ') : '',
      applications.length > 0 || (!loading && !loadError) ? plural(applications.length, 'applicant') : '',
    ].filter(Boolean).join(' · ');

    let list: ReactNode = null;
    if (applications.length > 0) {
      list = narrow ? (
        <ul aria-label="Applicants" style={{ ...CARD_LIST, gap: isPage ? 10 : 0 }}>
          {applications.map(app => {
            const facts = [
              app.candidate_source,
              app.rating != null && `Rating ${app.rating}`,
              timeAgo(app.applied_at),
            ].filter(Boolean).join(' · ');
            return (
              <li key={app.id}>
                <button type="button" onClick={() => goToCandidate(app)} style={cardRowStyle(isPage)}>
                  <CardRowTop name={app.candidate_name} badge={<StageBadge status={app.status} />} />
                  {app.candidate_email && <span style={{ display: 'block', marginTop: 2, overflowWrap: 'anywhere', ...META }}>{app.candidate_email}</span>}
                  {facts && <span style={{ display: 'block', marginTop: 2, ...META }}>{facts}</span>}
                </button>
              </li>
            );
          })}
        </ul>
      ) : (
        <table className="n-table">
          <thead>
            <tr>
              <th style={edgeL}>Candidate</th>
              <th>Source</th>
              <th>Stage</th>
              <th className="num">Rating</th>
              <th className="num">Applied</th>
              <th style={edgeR} />
            </tr>
          </thead>
          <tbody>
            {applications.map(app => (
              <tr key={app.id} onClick={() => goToCandidate(app)} style={{ cursor: 'pointer' }}>
                <td style={edgeL}>
                  <button type="button" style={ROW_NAME}>{app.candidate_name}</button>
                  {app.candidate_email && <div style={SUBLINE}>{app.candidate_email}</div>}
                </td>
                <td style={{ color: 'var(--text-soft)' }}>{app.candidate_source}</td>
                <td><StageBadge status={app.status} /></td>
                <td className="num">{app.rating}</td>
                <td className="num" style={{ color: 'var(--muted)', whiteSpace: 'nowrap' }}>{timeAgo(app.applied_at)}</td>
                <td style={{ width: 16, ...edgeR }}>{rowChevron}</td>
              </tr>
            ))}
          </tbody>
        </table>
      );
    }

    return (
      <div ref={measure}>
        {breadcrumb}
        <Frame page={isPage}>
          <Head
            page={isPage}
            titleOnPhone
            title={jobTitle}
            status={job ? <JobStatusBadge status={job.status} /> : undefined}
            meta={meta || undefined}
          />
          {errorState('Couldn’t load the applicants')}
          {loading && applications.length === 0 && <PageState kind="loading" title="Loading applicants…" />}
          {!loading && !loadError && applications.length === 0 && <PageState kind="empty" title="No applicants yet." />}
          {list}
        </Frame>
      </div>
    );
  }

  // --- Candidate Detail View ---
  if (view === 'candidate_detail' && selectedApp) {
    const detail = appDetail;
    const displayName = detail?.candidate_name || selectedApp.candidate_name;
    const displayEmail = detail?.candidate_email || selectedApp.candidate_email;
    const displaySource = detail?.candidate_source || selectedApp.candidate_source;
    const displayStatus = detail?.status || selectedApp.status;
    const displayApplied = detail?.applied_at || selectedApp.applied_at;

    const contact = [displayEmail, detail?.candidate_phone].filter(Boolean).join(' · ');
    const fields: { label: string; value: ReactNode }[] = [];
    if (displaySource) fields.push({ label: 'Source', value: displaySource });
    if (displayApplied) fields.push({ label: 'Applied', value: new Date(displayApplied).toLocaleDateString('en-NZ', { day: 'numeric', month: 'short', year: 'numeric' }) });
    if (detail?.hiring_lead) fields.push({ label: 'Hiring lead', value: detail.hiring_lead });
    if (detail?.desired_salary) fields.push({ label: 'Desired salary', value: detail.desired_salary });
    if (detail?.available_start_date) fields.push({ label: 'Available start', value: detail.available_start_date });
    if (detail?.linkedin_url) fields.push({ label: 'LinkedIn', value: <a href={detail.linkedin_url} target="_blank" rel="noopener noreferrer" style={LINK}>View profile</a> });
    if (detail?.website_url) fields.push({ label: 'Website', value: <a href={detail.website_url} target="_blank" rel="noopener noreferrer" style={LINK}>Visit</a> });
    if (detail?.education) fields.push({ label: 'Education', value: detail.education });

    const resumeButton = detail?.has_resume && detail.resume_file_id ? (
      <Button
        size={isPage ? 'md' : 'sm'}
        icon={Download}
        onClick={async (e) => {
          e.stopPropagation();
          const res = await apiFetch(`/api/connectors/bamboohr/files/${detail.resume_file_id}`);
          if (!res.ok) return;
          const blob = await res.blob();
          const url = URL.createObjectURL(blob);
          window.open(url, '_blank');
        }}
      >
        Download resume
      </Button>
    ) : undefined;

    return (
      <div ref={measure}>
        {breadcrumb}
        <Frame page={isPage}>
          <Head
            page={isPage}
            titleOnPhone
            title={displayName}
            status={<StageBadge status={displayStatus} />}
            meta={contact || undefined}
            actions={resumeButton}
          />

          {fields.length > 0 && (
            <Section page={isPage} title="Details">
              {fields.map((f, i) => (
                <Field key={f.label} label={f.label} stacked={narrow} first={i === 0}>{f.value}</Field>
              ))}
            </Section>
          )}

          {loading && !detail && <PageState kind="loading" title="Loading details…" />}
          {errorState('Couldn’t load this application')}

          {/* Questions & Answers */}
          {detail && detail.questions_and_answers.length > 0 && (
            <Section page={isPage} title="Screening questions">
              {detail.questions_and_answers.map((qa, i) => (
                <Field key={i} label={qa.question} stacked first={i === 0}>{qa.answer}</Field>
              ))}
            </Section>
          )}

          {detail && detail.comment_count > 0 && (
            <div style={{ ...META, ...(isPage ? { marginTop: 4 } : { padding: '10px 16px 14px', borderTop: '1px solid var(--line)' }) }}>
              {plural(detail.comment_count, 'comment')} on this application
            </div>
          )}
        </Frame>
      </div>
    );
  }

  // Fallback loading
  return <div ref={measure}><PageState kind="loading" title="Loading…" /></div>;
}
