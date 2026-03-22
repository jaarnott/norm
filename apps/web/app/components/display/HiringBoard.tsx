'use client';

import { useState, useEffect, useCallback } from 'react';
import type { DisplayBlockProps } from './DisplayBlockRenderer';
import { apiFetch } from '../../lib/api';

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

const STATUS_COLORS: Record<string, { bg: string; color: string }> = {
  open: { bg: '#d1fae5', color: '#065f46' },
  draft: { bg: '#fef3c7', color: '#92400e' },
  closed: { bg: '#e5e7eb', color: '#374151' },
  'on hold': { bg: '#fef3c7', color: '#92400e' },
  new: { bg: '#dbeafe', color: '#1e40af' },
  screening: { bg: '#fef3c7', color: '#92400e' },
  interview: { bg: '#ede9fe', color: '#5b21b6' },
  offer: { bg: '#d1fae5', color: '#065f46' },
  hired: { bg: '#d1fae5', color: '#065f46' },
  rejected: { bg: '#fee2e2', color: '#991b1b' },
};

function StatusBadge({ status }: { status: string }) {
  const key = status.toLowerCase();
  const s = STATUS_COLORS[key] || { bg: '#e5e7eb', color: '#374151' };
  return (
    <span style={{
      fontSize: '0.68rem', fontWeight: 600, padding: '2px 8px', borderRadius: 10,
      backgroundColor: s.bg, color: s.color, textTransform: 'capitalize', whiteSpace: 'nowrap',
    }}>{status}</span>
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

  const [view, setView] = useState<ViewMode>(initialJobId ? 'job_detail' : 'jobs');
  const [selectedJobId, setSelectedJobId] = useState<string | null>(initialJobId);
  const [selectedJobTitle, setSelectedJobTitle] = useState<string>('');

  const [jobs, setJobs] = useState<JobSummary[]>(() => extractJobs(data));
  const [jobFilter, setJobFilter] = useState<string>('all');
  const [applications, setApplications] = useState<ApplicationSummary[]>([]);
  const [selectedApp, setSelectedApp] = useState<ApplicationSummary | null>(null);
  const [appDetail, setAppDetail] = useState<ApplicationDetail | null>(null);
  const [loading, setLoading] = useState(false);

  // Load jobs on mount if not pre-populated
  const loadJobs = useCallback(async () => {
    setLoading(true);
    try {
      const result = await callConnector(connector, 'get_jobs');
      setJobs(extractJobs(result));
    } catch { /* ignore */ }
    finally { setLoading(false); }
  }, [connector]);

  useEffect(() => {
    if (jobs.length === 0) loadJobs();
  }, []);

  // Load applications for a job
  const loadApplications = useCallback(async (jobId: string) => {
    setLoading(true);
    try {
      const result = await callConnector(connector, 'get_applications', { job_id: jobId });
      setApplications(extractApplications(result));
    } catch { /* ignore */ }
    finally { setLoading(false); }
  }, [connector]);

  // Load full application detail
  const loadApplicationDetail = useCallback(async (applicationId: string) => {
    setLoading(true);
    try {
      const result = await callConnector(connector, 'get_application_details', { application_id: applicationId });
      setAppDetail(extractApplicationDetail(result));
    } catch { /* ignore */ }
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

  // --- Breadcrumb ---
  const breadcrumb = (
    <div style={{ display: 'flex', alignItems: 'center', gap: '0.3rem', fontSize: '0.78rem', marginBottom: '0.75rem' }}>
      <button onClick={goBackToJobs} style={{ border: 'none', background: 'none', cursor: 'pointer', color: '#2563eb', fontWeight: 500, fontFamily: 'inherit', fontSize: 'inherit', padding: 0 }}>Jobs</button>
      {view !== 'jobs' && (
        <>
          <span style={{ color: '#999' }}>/</span>
          <button onClick={goBackToJob} style={{ border: 'none', background: 'none', cursor: 'pointer', color: view === 'candidate_detail' ? '#2563eb' : '#333', fontWeight: 500, fontFamily: 'inherit', fontSize: 'inherit', padding: 0 }}>{selectedJobTitle}</button>
        </>
      )}
      {view === 'candidate_detail' && selectedApp && (
        <>
          <span style={{ color: '#999' }}>/</span>
          <span style={{ color: '#333', fontWeight: 500 }}>{selectedApp.candidate_name}</span>
        </>
      )}
    </div>
  );

  // --- Jobs View ---
  if (view === 'jobs') {
    const filteredJobs = jobFilter === 'all' ? jobs : jobs.filter(j => j.status === jobFilter);
    const statuses = ['all', ...Array.from(new Set(jobs.map(j => j.status)))];

    return (
      <div style={{ border: '1px solid #e5e7eb', borderRadius: 10, backgroundColor: '#fff', boxShadow: '0 1px 3px rgba(0,0,0,0.06)' }}>
        <div style={{ padding: '1rem 1.25rem', borderBottom: '1px solid #e5e7eb', background: 'linear-gradient(to bottom, #fafafa, #fff)' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '0.5rem' }}>
            <span style={{ fontSize: '1rem', fontWeight: 700, color: '#111' }}>Open Positions</span>
            <span style={{ fontSize: '0.75rem', color: '#999' }}>{jobs.length} job{jobs.length !== 1 ? 's' : ''}</span>
          </div>
          <div style={{ display: 'flex', gap: '0.3rem', flexWrap: 'wrap' }}>
            {statuses.map(f => (
              <button key={f} onClick={() => setJobFilter(f)} style={{
                padding: '0.2rem 0.6rem', fontSize: '0.72rem', fontWeight: jobFilter === f ? 600 : 400,
                borderRadius: 12, border: jobFilter === f ? '1px solid #2563eb' : '1px solid #e0e0e0',
                backgroundColor: jobFilter === f ? '#eff6ff' : '#fff', color: jobFilter === f ? '#2563eb' : '#666',
                cursor: 'pointer', fontFamily: 'inherit', textTransform: 'capitalize',
              }}>{f}</button>
            ))}
          </div>
        </div>

        <div>
          {loading && jobs.length === 0 && (
            <div style={{ padding: '2rem', textAlign: 'center', color: '#999' }}>Loading...</div>
          )}
          {!loading && filteredJobs.length === 0 && (
            <div style={{ padding: '2rem', textAlign: 'center', color: '#999', fontSize: '0.85rem' }}>
              {jobs.length === 0 ? 'No positions found.' : 'No positions match this filter.'}
            </div>
          )}
          {filteredJobs.map((job, i) => (
            <div
              key={job.id}
              onClick={() => goToJob(job)}
              style={{
                display: 'flex', alignItems: 'center', gap: '0.75rem',
                padding: '0.75rem 1.25rem', cursor: 'pointer',
                borderBottom: i < filteredJobs.length - 1 ? '1px solid #f3f4f6' : 'none',
                transition: 'background-color 0.1s',
              }}
              onMouseEnter={e => (e.currentTarget.style.backgroundColor = '#f9fafb')}
              onMouseLeave={e => (e.currentTarget.style.backgroundColor = '')}
            >
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontWeight: 600, color: '#111', fontSize: '0.88rem' }}>{job.title}</div>
                <div style={{ fontSize: '0.72rem', color: '#6b7280' }}>
                  {[job.department, job.location].filter(Boolean).join(' · ')}
                  {job.hiring_lead && <span> · Lead: {job.hiring_lead}</span>}
                </div>
              </div>
              <StatusBadge status={job.status} />
              <div style={{ fontSize: '0.75rem', color: '#6b7280', minWidth: 90, textAlign: 'right' }}>
                {job.candidate_count} applicant{job.candidate_count !== 1 ? 's' : ''}
              </div>
              <div style={{ fontSize: '0.72rem', color: '#999', minWidth: 60, textAlign: 'right' }}>
                {timeAgo(job.posted_date)}
              </div>
            </div>
          ))}
        </div>
      </div>
    );
  }

  // --- Job Detail View (Candidates) ---
  if (view === 'job_detail') {
    return (
      <div>
        {breadcrumb}
        <div style={{ border: '1px solid #e5e7eb', borderRadius: 10, backgroundColor: '#fff', boxShadow: '0 1px 3px rgba(0,0,0,0.06)' }}>
          <div style={{ padding: '1rem 1.25rem', borderBottom: '1px solid #e5e7eb', background: 'linear-gradient(to bottom, #fafafa, #fff)' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '0.25rem' }}>
              <span style={{ fontSize: '1.1rem', fontWeight: 700, color: '#111' }}>{selectedJobTitle}</span>
              {jobs.find(j => j.id === selectedJobId) && (
                <StatusBadge status={jobs.find(j => j.id === selectedJobId)!.status} />
              )}
            </div>
          </div>

          <div style={{ padding: '0.75rem 1.25rem', display: 'flex', alignItems: 'center', justifyContent: 'space-between', borderBottom: '1px solid #f3f4f6' }}>
            <span style={{ fontSize: '0.82rem', fontWeight: 600, color: '#333' }}>
              Applicants ({applications.length})
            </span>
          </div>

          <div>
            {loading && applications.length === 0 && (
              <div style={{ padding: '2rem', textAlign: 'center', color: '#999' }}>Loading applicants...</div>
            )}
            {!loading && applications.length === 0 && (
              <div style={{ padding: '2rem', textAlign: 'center', color: '#999', fontSize: '0.85rem' }}>
                No applicants yet.
              </div>
            )}
            {applications.map((app, i) => (
              <div
                key={app.id}
                onClick={() => goToCandidate(app)}
                style={{
                  display: 'flex', alignItems: 'center', gap: '0.75rem',
                  padding: '0.65rem 1.25rem', cursor: 'pointer',
                  borderBottom: i < applications.length - 1 ? '1px solid #f3f4f6' : 'none',
                  transition: 'background-color 0.1s',
                }}
                onMouseEnter={e => (e.currentTarget.style.backgroundColor = '#f9fafb')}
                onMouseLeave={e => (e.currentTarget.style.backgroundColor = '')}
              >
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontWeight: 500, color: '#111', fontSize: '0.85rem' }}>{app.candidate_name}</div>
                  {app.candidate_email && <div style={{ fontSize: '0.72rem', color: '#6b7280' }}>{app.candidate_email}</div>}
                </div>
                {app.candidate_source && (
                  <span style={{ fontSize: '0.68rem', color: '#6b7280', backgroundColor: '#f3f4f6', padding: '1px 6px', borderRadius: 8 }}>
                    {app.candidate_source}
                  </span>
                )}
                <StatusBadge status={app.status} />
                {app.rating != null && (
                  <span style={{ fontSize: '0.78rem', fontWeight: 600, color: '#333' }}>
                    {app.rating}
                  </span>
                )}
                <div style={{ fontSize: '0.72rem', color: '#999', minWidth: 60, textAlign: 'right' }}>
                  {timeAgo(app.applied_at)}
                </div>
              </div>
            ))}
          </div>
        </div>
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

    return (
      <div>
        {breadcrumb}
        <div style={{ border: '1px solid #e5e7eb', borderRadius: 10, backgroundColor: '#fff', boxShadow: '0 1px 3px rgba(0,0,0,0.06)' }}>
          {/* Header */}
          <div style={{ padding: '1rem 1.25rem', borderBottom: '1px solid #e5e7eb', background: 'linear-gradient(to bottom, #fafafa, #fff)' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '0.3rem' }}>
              <div style={{ fontSize: '1.1rem', fontWeight: 700, color: '#111' }}>{displayName}</div>
              <StatusBadge status={displayStatus} />
              {detail?.has_resume && detail.resume_file_id && (
                <button
                  onClick={async (e) => {
                    e.stopPropagation();
                    const res = await apiFetch(`/api/connectors/bamboohr/files/${detail.resume_file_id}`);
                    if (!res.ok) return;
                    const blob = await res.blob();
                    const url = URL.createObjectURL(blob);
                    window.open(url, '_blank');
                  }}
                  style={{ fontSize: '0.68rem', fontWeight: 500, padding: '2px 8px', borderRadius: 10, backgroundColor: '#ede9fe', color: '#5b21b6', border: 'none', cursor: 'pointer', fontFamily: 'inherit' }}
                >
                  Download Resume
                </button>
              )}
            </div>
            <div style={{ display: 'flex', gap: '1.5rem', fontSize: '0.82rem', color: '#555', flexWrap: 'wrap' }}>
              {displayEmail && (
                <div><span style={{ color: '#999', fontSize: '0.72rem' }}>Email </span><span style={{ fontWeight: 500 }}>{displayEmail}</span></div>
              )}
              {detail?.candidate_phone && (
                <div><span style={{ color: '#999', fontSize: '0.72rem' }}>Phone </span><span style={{ fontWeight: 500 }}>{detail.candidate_phone}</span></div>
              )}
              {displaySource && (
                <div><span style={{ color: '#999', fontSize: '0.72rem' }}>Source </span><span style={{ fontWeight: 500 }}>{displaySource}</span></div>
              )}
              {displayApplied && (
                <div><span style={{ color: '#999', fontSize: '0.72rem' }}>Applied </span><span style={{ fontWeight: 500 }}>{new Date(displayApplied).toLocaleDateString('en-NZ', { day: 'numeric', month: 'short', year: 'numeric' })}</span></div>
              )}
            </div>
          </div>

          {loading && !detail && (
            <div style={{ padding: '2rem', textAlign: 'center', color: '#999' }}>Loading details...</div>
          )}

          {detail && (
            <div style={{ padding: '1rem 1.25rem' }}>
              {/* Key info row */}
              <div style={{ display: 'flex', gap: '1.5rem', flexWrap: 'wrap', marginBottom: '1rem' }}>
                {detail.hiring_lead && (
                  <div>
                    <div style={{ fontSize: '0.65rem', color: '#6b7280', fontWeight: 600, textTransform: 'uppercase', marginBottom: 3 }}>Hiring Lead</div>
                    <span style={{ fontSize: '0.82rem', color: '#333' }}>{detail.hiring_lead}</span>
                  </div>
                )}
                {detail.desired_salary && (
                  <div>
                    <div style={{ fontSize: '0.65rem', color: '#6b7280', fontWeight: 600, textTransform: 'uppercase', marginBottom: 3 }}>Desired Salary</div>
                    <span style={{ fontSize: '0.82rem', color: '#333' }}>{detail.desired_salary}</span>
                  </div>
                )}
                {detail.available_start_date && (
                  <div>
                    <div style={{ fontSize: '0.65rem', color: '#6b7280', fontWeight: 600, textTransform: 'uppercase', marginBottom: 3 }}>Available Start</div>
                    <span style={{ fontSize: '0.82rem', color: '#333' }}>{detail.available_start_date}</span>
                  </div>
                )}
                {detail.linkedin_url && (
                  <div>
                    <div style={{ fontSize: '0.65rem', color: '#6b7280', fontWeight: 600, textTransform: 'uppercase', marginBottom: 3 }}>LinkedIn</div>
                    <a href={detail.linkedin_url} target="_blank" rel="noopener noreferrer" style={{ fontSize: '0.82rem', color: '#2563eb' }}>View Profile</a>
                  </div>
                )}
                {detail.website_url && (
                  <div>
                    <div style={{ fontSize: '0.65rem', color: '#6b7280', fontWeight: 600, textTransform: 'uppercase', marginBottom: 3 }}>Website</div>
                    <a href={detail.website_url} target="_blank" rel="noopener noreferrer" style={{ fontSize: '0.82rem', color: '#2563eb' }}>Visit</a>
                  </div>
                )}
                {detail.education && (
                  <div>
                    <div style={{ fontSize: '0.65rem', color: '#6b7280', fontWeight: 600, textTransform: 'uppercase', marginBottom: 3 }}>Education</div>
                    <span style={{ fontSize: '0.82rem', color: '#333' }}>{detail.education}</span>
                  </div>
                )}
              </div>

              {/* Questions & Answers */}
              {detail.questions_and_answers.length > 0 && (
                <div style={{ marginTop: '0.5rem' }}>
                  <div style={{ fontSize: '0.78rem', fontWeight: 600, color: '#333', marginBottom: '0.5rem' }}>Screening Questions</div>
                  {detail.questions_and_answers.map((qa, i) => (
                    <div key={i} style={{
                      padding: '0.5rem 0.75rem', marginBottom: '0.4rem',
                      backgroundColor: '#f9fafb', borderRadius: 6, border: '1px solid #f3f4f6',
                    }}>
                      <div style={{ fontSize: '0.78rem', color: '#6b7280', marginBottom: 2 }}>{qa.question}</div>
                      <div style={{ fontSize: '0.85rem', color: '#111', fontWeight: 500 }}>{qa.answer}</div>
                    </div>
                  ))}
                </div>
              )}

              {/* Stats */}
              {detail.comment_count > 0 && (
                <div style={{ marginTop: '0.75rem', fontSize: '0.78rem', color: '#6b7280' }}>
                  {detail.comment_count} comment{detail.comment_count !== 1 ? 's' : ''} on this application
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    );
  }

  // Fallback loading
  return <div style={{ padding: '2rem', textAlign: 'center', color: '#999' }}>Loading...</div>;
}
