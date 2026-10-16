/**
 * Norm Functions Inbox — typed calls to `/api/functions/inbox/*` (F4) and the
 * reply templates (F3). Thin on purpose: errors surface as `FnApiError`.
 */
import { apiFetch } from '../../../lib/api';
import { FnApiError, fnGet, fnSend, qs } from '../shared/api';
import { filenameFromDisposition } from './text';
import type {
  ComposeMode,
  Draft,
  Folder,
  LiveSearchRow,
  MailboxesResponse,
  OutboxResult,
  PreparedCompose,
  ReplyTemplate,
  ThreadDetail,
  ThreadsResponse,
} from './types';

const p = encodeURIComponent;
const thread = (accountId: string, threadId: string) => `/inbox/threads/${p(accountId)}/${p(threadId)}`;

export interface ThreadQuery {
  venue_id?: string;
  account_id?: string;
  folder?: Folder | 'unfiled';
  linked?: boolean;
  unread?: boolean;
  q?: string;
  limit?: number;
  offset?: number;
}

export interface DraftWrite {
  mode: ComposeMode;
  to: string[];
  cc: string[];
  bcc: string[];
  subject: string;
  body_html: string;
}

export const inboxApi = {
  mailboxes: () => fnGet<MailboxesResponse>('/inbox/mailboxes'),
  /** "Sync now" for every visible connected mailbox (fresh ones are skipped). */
  syncNow: () => fnSend<{ results: { account_id: string; ok?: boolean; messages?: number }[] }>('POST', '/inbox/sync', {}),

  threads: (query: ThreadQuery) => fnGet<ThreadsResponse>('/inbox/threads', { ...query }),
  thread: (accountId: string, threadId: string) => fnGet<ThreadDetail>(thread(accountId, threadId)),
  setFolder: (accountId: string, threadId: string, folder: Folder | null) =>
    fnSend<{ folder: Folder | null; gmail_synced: boolean; gmail_error: string | null }>(
      'PUT',
      `${thread(accountId, threadId)}/folder`,
      { folder },
    ),
  link: (accountId: string, threadId: string, target: { enquiry_id: string } | { event_id: string }) =>
    fnSend<{ link_id: string }>('POST', `${thread(accountId, threadId)}/links`, target),
  unlink: (linkId: string) => fnSend<{ link_id: string }>('DELETE', `/inbox/links/${p(linkId)}`),
  createEnquiry: (
    accountId: string,
    threadId: string,
    body: { booking_type?: string; subject?: string; venue_id?: string; contact_name?: string; contact_email?: string },
  ) => fnSend<{ enquiry_id: string; contact_id: string; link_id: string }>('POST', `${thread(accountId, threadId)}/enquiry`, body),
  /** Live Gmail search (Gmail's syntax) for mail older than the mirror. */
  searchGmail: (accountId: string, q: string) =>
    fnGet<{ threads: LiveSearchRow[] }>('/inbox/search', { account_id: accountId, q, limit: 20 }),

  prepare: (body: { account_id: string; mode: ComposeMode; gmail_thread_id?: string | null; message_id?: string | null }) =>
    fnSend<PreparedCompose>('POST', '/inbox/compose/prepare', body),
  composeSend: (body: {
    account_id: string;
    mode: ComposeMode;
    to: string[];
    cc: string[];
    bcc: string[];
    subject?: string | null;
    body_html: string;
    gmail_thread_id?: string | null;
    in_reply_to_gmail_message_id?: string | null;
    enquiry_id?: string | null;
    event_id?: string | null;
    idempotency_key: string;
    price_override_reason?: string;
  }) => fnSend<OutboxResult>('POST', '/inbox/compose/send', body),

  createDraft: (
    body: DraftWrite & {
      account_id: string;
      gmail_thread_id?: string | null;
      in_reply_to_gmail_message_id?: string | null;
      enquiry_id?: string | null;
      event_id?: string | null;
      kind: 'email';
    },
  ) => fnSend<Draft>('POST', '/inbox/drafts', body),
  updateDraft: (id: string, version: number, body: DraftWrite) =>
    fnSend<Draft>('PATCH', `/inbox/drafts/${p(id)}`, { expected_version: version, ...body }),
  discardDraft: (id: string, version: number) =>
    fnSend<Draft>('DELETE', `/inbox/drafts/${p(id)}${qs({ expected_version: version })}`),
  sendDraft: (id: string, body: { idempotency_key: string; price_override_reason?: string }) =>
    fnSend<OutboxResult>('POST', `/inbox/drafts/${p(id)}/send`, body),
  draftToGmail: (id: string, version: number) =>
    fnSend<Draft>('POST', `/inbox/drafts/${p(id)}/send-to-gmail`, { expected_version: version }),
  bringBack: (id: string) => fnSend<Draft>('POST', `/inbox/drafts/${p(id)}/bring-back`),

  /** One attachment, live from Gmail. The route needs the bearer token, so
   *  it is fetched (not linked) and handed back as a blob to save. */
  attachment: async (accountId: string, threadId: string, messageId: string, ref: string, fallbackName: string) => {
    const res = await apiFetch(`/api/functions${thread(accountId, threadId)}/messages/${p(messageId)}/attachments/${p(ref)}`);
    if (!res.ok) {
      let code: string | null = null;
      let message = res.status === 413 ? 'Too large to download here — open it in Gmail.' : `Download failed (${res.status}).`;
      try {
        const detail = (await res.json())?.detail;
        if (detail && typeof detail === 'object') {
          code = typeof detail.code === 'string' ? detail.code : null;
          if (typeof detail.message === 'string') message = detail.message;
        }
      } catch {
        /* not JSON */
      }
      throw new FnApiError(res.status, code, message, null);
    }
    return { blob: await res.blob(), filename: filenameFromDisposition(res.headers.get('Content-Disposition'), fallbackName) };
  },

  replyTemplates: (query: { venue_id?: string | null; booking_type?: string | null }) =>
    fnGet<{ rows: ReplyTemplate[] }>('/reply-templates', { ...query }),
};
