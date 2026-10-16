"""Venue mailboxes — the Gmail side of Norm Functions.

Mail is mirrored as METADATA (``fn_mailbox_threads`` / ``fn_mailbox_messages``:
who, when, which labels, a snippet) and bodies are fetched live when someone
opens a thread. The mirror rows are facts ``status.py`` reads — an outbound
message on a thread linked to an enquiry is what makes it "replied" — so the
three message flags (``is_inbound``, ``is_internal``, ``is_auto_reply``) are
decided in exactly one place, ``classify.py``.

The pieces, and the failure each one is built around:

* ``gmail_client`` — Gmail's REST API, one token refresh per 401 through
  Norm's own OAuth machinery, errors sorted into "gone", "try again later"
  and "a person must reconnect". Writes refuse unless the org is live.
* ``sync`` — every minute (``/internal/sync-mailboxes``): a lease per mailbox
  so two workers never sync one mailbox, a cursor that only moves past what
  was actually written (Orbit's dropped everything after five pages), a
  resumable initial sync, parked messages that would otherwise block the
  cursor forever, and health that pages someone when a mailbox lags.
* ``outbox`` — every send is written and committed BEFORE Gmail is called,
  keyed by the caller's idempotency key and carrying our own Message-ID, so a
  double click or a lost response is one email, and a reconciler can tell
  "sent" from "never sent" afterwards.
* ``drafts`` — drafts live in Norm and can be handed to Gmail; one owner at
  a time, so the two copies never fight.
* ``accounts`` — connecting a venue's mailbox (Norm's own OAuth connector
  ``venue_mailbox``), the mailbox list and its health, the reconnect email.
* ``inbox`` — what the Inbox screen and the agent read and do: thread lists,
  live thread bodies, folders, links, enquiries from threads, live search.
* ``sending`` — proposal sends and "send to client" (portal link) through
  the outbox, recording the facts that move an enquiry to "quoted" and an
  event to "sent to client".

Every function takes ``(db, ctx, …)`` like the rest of Functions; services
flush and routers commit — except where a Gmail call sits in the middle,
where these modules commit before the call (no DB connection is held across
Gmail) and say so.
"""
