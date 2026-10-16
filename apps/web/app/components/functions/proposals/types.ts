/**
 * Norm Functions proposals — response shapes of F3's proposal endpoints
 * (`/api/functions/enquiries/{id}/proposal…`, scratchpad F3-api.md §9) and
 * F4's proposal send.
 */
import type { OutboxResult } from '../inbox/types';

export interface Proposal {
  id: string;
  version: number;
  enquiry_id: string;
  /** Stored sanitised: what comes back is what customers see. */
  html: string;
  css: string;
  based_on_brand_version: number | null;
  updated_by_user_id: string | null;
  updated_by_label: string | null;
  updated_at: string | null;
}

export interface ProposalSummary {
  exists: boolean;
  id: string | null;
  version: number | null;
  updated_at: string | null;
  versions: number;
  last_sent_at: string | null;
  last_viewed_at: string | null;
  based_on_brand_version: number | null;
  brand_changed: boolean;
}

/** The money check: dollar figures in the HTML vs what's on record. Never blocks. */
export interface MoneyCheck {
  ok: boolean;
  stated_cents: number[];
  unmatched_cents: number[];
  recorded: { label: string; cents: number }[];
  warnings: string[];
}

/** An option as a proposal offers it (live, non-declined minimum spends). */
export interface OptionSnapshot {
  min_spend_id: string;
  label: string | null;
  venue_id: string | null;
  area_id: string | null;
  area_name: string | null;
  event_date: string | null;
  start_time: string | null;
  end_time: string | null;
  ends_next_day: boolean;
  amount_cents: number;
  food_cents: number;
  total_cents: number;
  state: string;
  confirmable: boolean;
}

export interface ProposalPayload {
  proposal: Proposal | null;
  summary: ProposalSummary;
  check: MoneyCheck | null;
  options: OptionSnapshot[];
}

export interface ProposalVersion {
  id: string;
  proposal_id: string;
  enquiry_id: string;
  version_number: number;
  options: OptionSnapshot[];
  sent_at: string | null;
  sent_by_user_id: string | null;
  sent_by_label: string | null;
  created_at: string | null;
}

export interface LibraryImage {
  id: string;
  version: number;
  venue_id: string;
  file_id: string;
  name: string | null;
  caption: string | null;
  tags: string[];
  created_at: string | null;
}

/** The parts of EnquiryDetail the proposal screen uses. */
export interface EnquiryForProposal {
  enquiry: {
    id: string;
    subject: string | null;
    venue_id: string | null;
    booking_type: string | null;
    guests: number | null;
  };
  stage: string;
  venue: { id: string; name: string; timezone: string | null } | null;
  contact: { name: string | null; email?: string | null; emails?: { email: string }[] } | null;
  threads: {
    link_id: string;
    account_id: string;
    mailbox: string;
    venue_id: string | null;
    gmail_thread_id: string;
    subject: string | null;
    last_message_at: string | null;
  }[];
}

export interface ProposalSendResult extends OutboxResult {
  mode: 'proposal' | 'email';
  quote_url: string | null;
  proposal_version_id: string | null;
  account_id: string;
}
