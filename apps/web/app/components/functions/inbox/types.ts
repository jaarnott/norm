/**
 * Norm Functions Inbox — response shapes of `/api/functions/inbox/*` (F4's
 * router, scratchpad F4-api.md) and the reply templates it uses (F3).
 */

export type Folder = 'enquiry' | 'reservation' | 'other';
export const FOLDERS: Folder[] = ['enquiry', 'reservation', 'other'];

export type HealthState = 'connected' | 'syncing' | 'needs_reconnect' | 'not_connected' | 'disabled';

export interface MailboxHealth {
  state: HealthState;
  connected: boolean;
  last_ok_at: string | null;
  last_error: string | null;
  consecutive_failures: number;
  lag_seconds: number | null;
  lagging: boolean;
}

export interface Mailbox {
  id: string;
  venue_id: string;
  venue_name: string | null;
  google_email: string;
  display_name: string | null;
  status: string;
  version: number;
  health: MailboxHealth;
  /** Which "Actioned - …" labels exist in the mailbox (filing reaches Gmail). */
  folders: Partial<Record<Folder, boolean>>;
  has_signature: boolean;
}

export interface MailboxesResponse {
  venues: { venue_id: string; venue_name: string; mailbox_ids: string[] }[];
  mailboxes: Mailbox[];
}

export interface ThreadLink {
  link_id: string;
  /** false = linked to a record at a venue the caller can't see (no details). */
  visible: boolean;
  enquiry_id?: string | null;
  event_id?: string | null;
  label?: string | null;
  contact_name?: string | null;
}

export interface ThreadRow {
  account_id: string;
  mailbox: string;
  venue_id: string;
  venue_name: string | null;
  gmail_thread_id: string;
  subject: string | null;
  snippet: string | null;
  last_message_at: string | null;
  last_from_email: string | null;
  last_from_name: string | null;
  message_count: number;
  unread_count: number;
  in_inbox: boolean;
  folder: Folder | null;
  links: ThreadLink[];
  linked: boolean;
  /** The customer wrote last (internal mail and auto-replies ignored). */
  awaiting_reply: boolean;
  has_norm_draft: boolean;
}

export interface ThreadsResponse {
  threads: ThreadRow[];
  has_more: boolean;
}

export interface Attachment {
  filename: string;
  mime_type: string;
  size: number;
  attachment_id: string;
  /** The MIME part id — the stable reference for a download (Gmail's
   *  attachment ids can change between fetches). */
  part_id?: string | null;
}

export interface Message {
  gmail_message_id: string;
  internal_date: string | null;
  from_email: string | null;
  from_name: string | null;
  to: string[];
  cc: string[];
  subject: string | null;
  snippet: string | null;
  is_inbound: boolean | null;
  is_internal: boolean | null;
  is_auto_reply: boolean | null;
  unread: boolean | null;
  /** Sanitised server-side; still rendered in a sandboxed iframe. */
  html: string | null;
  text: string | null;
  attachments: Attachment[];
}

export interface Draft {
  id: string;
  version: number;
  kind: 'email' | 'proposal';
  /** How it goes out; null on older drafts = reply with a thread, else new. */
  mode?: ComposeMode | null;
  status: 'open' | 'sent' | 'discarded';
  /** norm = editable here; gmail = handed to the mailbox, locked here. */
  owner: 'norm' | 'gmail';
  locked: boolean;
  account_id: string | null;
  enquiry_id: string | null;
  event_id: string | null;
  gmail_thread_id: string | null;
  in_reply_to_gmail_message_id: string | null;
  to: string[];
  cc: string[];
  bcc: string[];
  subject: string | null;
  body_html: string | null;
  gmail_draft_id: string | null;
  handed_to_gmail_at: string | null;
  sent_at: string | null;
  sent_gmail_message_id: string | null;
  created_by_label: string | null;
  updated_at: string | null;
}

export interface ThreadDetail {
  account_id: string;
  mailbox: string;
  venue_id: string;
  gmail_thread_id: string;
  folder: Folder | null;
  folder_sync_error: string | null;
  links: ThreadLink[];
  drafts: Draft[];
  bodies_available: boolean;
  bodies_error: string | null;
  has_gmail_draft: boolean;
  messages: Message[];
}

export interface LiveSearchRow {
  account_id: string;
  gmail_thread_id: string;
  subject: string | null;
  snippet: string | null;
  last_message_at: string | null;
  last_from_email: string | null;
  last_from_name: string | null;
  message_count: number;
  mirrored: boolean;
}

export type ComposeMode = 'reply' | 'reply_all' | 'forward' | 'new';

export interface PreparedCompose {
  account_id: string;
  mode: ComposeMode;
  from: string;
  to: string[];
  cc: string[];
  subject: string;
  gmail_thread_id: string | null;
  in_reply_to_gmail_message_id: string | null;
  quoted_html: string | null;
  signature_html: string | null;
}

export interface OutboxResult {
  outbox_id: string;
  status: 'sent' | 'sending' | 'failed';
  rfc822_message_id: string;
  gmail_message_id: string | null;
  gmail_thread_id: string | null;
  duplicate: boolean;
  error: string | null;
  [extra: string]: unknown;
}

export type TemplateStage = 'first_reply' | 'follow_up';

export interface ReplyTemplate {
  id: string;
  version: number;
  venue_id: string | null;
  booking_type: string | null;
  stage: TemplateStage;
  name: string;
  subject: string | null;
  body_html: string;
  active: boolean;
  sort_order: number;
}
