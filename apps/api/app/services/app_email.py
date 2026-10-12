"""Email from an app's logic — the sandbox's ``email`` object (Oct 2026).

Built for Norm's own apps (training reminders, review digests, goals emails,
candidate interview invitations) and deliberately narrow:

* **Off unless an org admin turns it on** (``AppEmailSetting``, per org per
  app). While off, a live send is a no-op that says so — Orbit is still the
  live system for HR, and staff must not get every email twice.
* **Recipients belong to the org.** ``person:<id>`` resolves through the app's
  own ``people`` collection; a raw address must be an org member's or one of
  the app's people — unless the app's spec declares ``email.external`` (Hiring
  emails candidates, who are nobody's colleagues yet).
* **Never twice.** A ``key`` makes a send idempotent; the database enforces it
  (``uq_email_logs_app_key``), so two concurrent runs can't both send.
* **Bounded.** At most ``PER_RUN_LIMIT`` sends per run and ``PER_DAY_LIMIT``
  per org per app per day.
* **Safe to try.** ``dry_run`` renders without sending; ``test`` sends only to
  the person running it, subject prefixed "[TEST]". Outside production a live
  send is suppressed (logged, not sent) unless ``APP_EMAIL_ALLOW_NONPROD`` is
  set — local data is real staff data.

The app supplies the subject and the body HTML (it escapes its own values);
the platform supplies the layout, the From name (the org's) and the log.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta, timezone
from email.utils import formataddr
from urllib.parse import urlencode

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

PER_RUN_LIMIT = 300
PER_DAY_LIMIT = 1000
#: Live sends one PERSON may trigger in a day (scheduled runs are exempt) —
#: so one member can't spend the org's whole daily allowance.
PER_USER_DAY_LIMIT = 200
#: Test sends (to yourself) one person may make in a day — they bypass the
#: org's switch and limits, so they get their own small cap.
PER_USER_TEST_DAY_LIMIT = 20
#: A claim left at 'sending' this long is a crashed send; it stops blocking.
STALE_CLAIM = timedelta(minutes=30)
#: A key is taken while its email is being sent and once it's been sent;
#: a failed attempt frees it for a retry.
CLAIMED = ("sending", "sent")
MODES = ("live", "dry_run", "test")
_EMAIL = re.compile(r"^[^@\s<>,;]+@[^@\s<>,;]+\.[^@\s<>,;]+$")


_ALLOWED_TAGS = {
    "p",
    "br",
    "strong",
    "b",
    "em",
    "i",
    "u",
    "ul",
    "ol",
    "li",
    "a",
    "h1",
    "h2",
    "h3",
    "h4",
    "blockquote",
    "span",
    "div",
}


def sanitize_html(html: str) -> str:
    """Keep simple formatting from HTML a person typed (Hiring's composer):
    whitelisted tags, no attributes but an http(s)/mailto ``href``; text
    re-escaped. Scripts, styles, event handlers and everything else go."""
    from html import escape
    from html.parser import HTMLParser

    out: list[str] = []
    skip = [0]

    class P(HTMLParser):
        def handle_starttag(self, tag, attrs):
            if tag in ("script", "style"):
                skip[0] += 1
                return
            if skip[0] or tag not in _ALLOWED_TAGS:
                return
            if tag == "a":
                href = next((v for k, v in attrs if k == "href" and v), "")
                if href.lower().startswith(("http://", "https://", "mailto:")):
                    out.append(f'<a href="{escape(href, quote=True)}">')
                else:
                    out.append("<a>")
            elif tag == "br":
                out.append("<br>")
            else:
                out.append(f"<{tag}>")

        def handle_endtag(self, tag):
            if tag in ("script", "style"):
                skip[0] = max(0, skip[0] - 1)
                return
            if not skip[0] and tag in _ALLOWED_TAGS and tag != "br":
                out.append(f"</{tag}>")

        def handle_data(self, data):
            if not skip[0]:
                out.append(escape(data))

    parser = P(convert_charrefs=True)
    parser.feed(str(html or ""))
    parser.close()
    return "".join(out)


class _Borrowed:
    """The run's own session standing in for an independent one, when the
    caller manages the outer transaction itself (a session bound to an
    explicit Connection — the test harness). Commits become flushes."""

    def __init__(self, db):
        self._db = db

    def __getattr__(self, name):
        return getattr(self._db, name)

    def commit(self):
        self._db.flush()

    def rollback(self):
        pass

    def close(self):
        pass


def _independent_session(db: Session):
    """The email log is written OUTSIDE the run's transaction: a key is
    claimed (and the outcome recorded) whether or not the run later fails,
    so a retry never sends twice and never loses what was sent."""
    from sqlalchemy.engine import Connection

    if isinstance(db.get_bind(), Connection):
        return _Borrowed(db)
    from app.db.engine import SessionLocal

    return SessionLocal()


def email_enabled(db: Session, organization_id: str, app_slug: str) -> bool:
    from app.db.models import AppEmailSetting

    row = (
        db.query(AppEmailSetting)
        .filter(
            AppEmailSetting.organization_id == organization_id,
            AppEmailSetting.app_slug == app_slug,
        )
        .first()
    )
    return bool(row and row.enabled)


def set_email_enabled(
    db: Session, organization_id: str, app_slug: str, enabled: bool, user_id: str | None
) -> None:
    from app.db.models import AppEmailSetting

    row = (
        db.query(AppEmailSetting)
        .filter(
            AppEmailSetting.organization_id == organization_id,
            AppEmailSetting.app_slug == app_slug,
        )
        .first()
    )
    if row is None:
        row = AppEmailSetting(organization_id=organization_id, app_slug=app_slug)
        db.add(row)
    row.enabled = bool(enabled)
    row.updated_by = user_id
    db.flush()


def _nonprod_suppressed() -> bool:
    from app.config import settings

    return settings.ENVIRONMENT != "production" and not os.environ.get(
        "APP_EMAIL_ALLOW_NONPROD"
    )


class AppEmail:
    """The ``email`` object an app's ``run()`` sees. Bound to one app, one org
    and one viewer by ``app_runtime.run_logic``; the sandbox can't rebind it."""

    def __init__(self, db: Session, *, app, version, user, mode: str, person_email):
        self.db = db
        self.app = app
        self.version = version
        self.user = user
        self.mode = mode if mode in MODES else "live"
        self.person_email = person_email  # callable(person_id) -> str | None
        self.sent_this_run = 0
        cfg = (version.spec or {}).get("email") or {}
        self.external = bool(cfg.get("external"))

    # -- what the app can ask ------------------------------------------------
    def enabled(self) -> bool:
        return email_enabled(self.db, self.app.organization_id, self.app.slug)

    def _may(self, *perms: str) -> bool:
        from app.services.app_runtime import is_system, org_permissions

        if is_system(self.user):
            return False
        if getattr(self.user, "role", None) == "admin":
            return True
        held = org_permissions(self.db, self.user)
        return any(p in held for p in perms)

    def set_enabled(self, enabled) -> dict:
        """Turn this app's email on or off for the org — org managers only,
        and never from a scheduled run."""
        if not self._may("org:manage"):
            return {"ok": False, "reason": "only an org manager can change this"}
        set_email_enabled(
            self.db,
            self.app.organization_id,
            self.app.slug,
            bool(enabled),
            getattr(self.user, "id", None),
        )
        return {"ok": True, "enabled": bool(enabled)}

    def log(self, limit=200) -> list[dict]:
        """This app's recent emails in this org (no bodies) — for its log page."""
        from app.db.models import EmailLog

        if not self._may("email:read", "org:manage"):
            return []
        rows = (
            self.db.query(EmailLog)
            .filter(
                EmailLog.organization_id == self.app.organization_id,
                EmailLog.app_slug == self.app.slug,
            )
            .order_by(EmailLog.created_at.desc())
            .limit(max(1, min(int(limit or 200), 500)))
            .all()
        )
        return [
            {
                "id": r.id,
                "to": r.to_addresses,
                "cc": r.cc_addresses,
                "subject": r.subject,
                "kind": (r.template_name or "").split(":")[-1],
                "status": r.status,
                "error": r.error_message,
                "is_test": bool(r.is_test),
                "at": (r.sent_at or r.created_at).isoformat()
                if (r.sent_at or r.created_at)
                else None,
            }
            for r in rows
        ]

    def preview(self, log_id) -> str | None:
        """One logged email's HTML — same permission as the log."""
        from app.db.models import EmailLog

        if not self._may("email:read", "org:manage"):
            return None
        row = (
            self.db.query(EmailLog)
            .filter(
                EmailLog.id == str(log_id),
                EmailLog.organization_id == self.app.organization_id,
                EmailLog.app_slug == self.app.slug,
            )
            .first()
        )
        return row.html_body if row else None

    def link(self, inputs: dict | None = None) -> str:
        """An absolute link that opens this app at ``inputs`` (see
        services/app_components.py) — for the email's button."""
        from app.config import settings

        query = urlencode(
            {k: v for k, v in (inputs or {}).items() if v not in (None, "")}
        )
        return f"{settings.app_url}/apps/{self.app.slug}" + (
            f"?{query}" if query else ""
        )

    def send(
        self,
        to,
        subject,
        html,
        key=None,
        kind=None,
        cc=None,
        reply_to=None,
        heading=None,
        button=None,
        sanitize=False,
    ) -> dict:
        """Send one email (to one or more recipients). Returns a dict with
        ``status``: sent | skipped | disabled | dry_run | suppressed | failed.
        ``sanitize=True`` for a body a person typed (keeps simple formatting
        only — see :func:`sanitize_html`)."""
        # No header injection: a subject is one line.
        subject = " ".join(str(subject or "").split())
        if not subject:
            return {"status": "failed", "reason": "a subject is required"}
        recipients, refused = self._resolve(to)
        cc_list, cc_refused = self._resolve(cc or [])
        refused += cc_refused
        if not recipients:
            return {
                "status": "failed",
                "reason": "no deliverable recipient",
                "refused": refused,
            }
        reply = str(reply_to or "").strip() or None
        if reply and not _EMAIL.match(reply):
            reply = None

        if sanitize:
            html = sanitize_html(html)
        rendered = self._render(subject, html, heading=heading, button=button)

        if self.mode == "dry_run":
            return {
                "status": "dry_run",
                "to": recipients,
                "cc": cc_list,
                "subject": subject,
                "html": rendered,
                "refused": refused,
            }

        is_test = self.mode == "test"
        if is_test:
            me = getattr(self.user, "email", None)
            if not me:
                return {
                    "status": "failed",
                    "reason": "test mode needs a signed-in person",
                }
            if self._tests_today_by_me() >= PER_USER_TEST_DAY_LIMIT:
                return {
                    "status": "limited",
                    "reason": "your daily test-send limit was reached",
                }
            recipients, cc_list, subject = [me], [], f"[TEST] {subject}"
            key = None  # a test never claims (or spends) a real key
        else:
            if not self.enabled():
                return {"status": "disabled", "reason": "email is off for this app"}
            if key:
                # Before the "already sent?" check: a crashed claim must not
                # keep counting as sent.
                stale = _independent_session(self.db)
                try:
                    self._release_stale_claim(stale, key)
                finally:
                    stale.close()
            if key and self._already_sent(key):
                return {"status": "skipped", "reason": "already sent", "key": key}
            if (
                self.sent_this_run >= PER_RUN_LIMIT
                or self._sent_today() >= PER_DAY_LIMIT
            ):
                return {
                    "status": "limited",
                    "reason": "the daily send limit was reached",
                }
            if self._sent_today_by_me() >= PER_USER_DAY_LIMIT:
                return {
                    "status": "limited",
                    "reason": "your daily send limit was reached",
                }

        suppressed = not is_test and _nonprod_suppressed()
        logs = _independent_session(self.db)
        try:
            # Claim first, in its own committed row: the unique index covers
            # 'sending' too, so a second run racing for the same key fails
            # HERE — before anything is delivered — and the run's own
            # transaction is never touched.
            log = self._log(
                logs,
                recipients,
                cc_list,
                subject,
                rendered,
                key=key,
                kind=kind,
                is_test=is_test,
                status="suppressed" if suppressed else "sending",
            )
            try:
                logs.commit()
            except IntegrityError:
                logs.rollback()
                return {"status": "skipped", "reason": "already sent", "key": key}
            if suppressed:
                log.error_message = "live sends are suppressed outside production"
                logs.commit()
                return {"status": "suppressed", "to": recipients, "id": log.id}

            status, error, provider_id = self._deliver(
                recipients, cc_list, reply, subject, rendered
            )
            log.status = status
            log.error_message = error
            log.provider_message_id = provider_id
            if status == "sent":
                log.sent_at = datetime.now(timezone.utc)
                self.sent_this_run += 1
            logs.commit()
            out = {"status": status, "to": recipients, "id": log.id}
            if error:
                out["reason"] = error
            if refused:
                out["refused"] = refused
            return out
        finally:
            logs.close()

    # -- internals -------------------------------------------------------------
    def _resolve(self, to) -> tuple[list[str], list[str]]:
        items = [to] if isinstance(to, str) else list(to or [])
        ok: list[str] = []
        refused: list[str] = []
        for item in items:
            raw = str(item or "").strip()
            if raw.startswith("person:"):
                addr = (self.person_email(raw.split(":", 1)[1]) or "").strip()
                if addr and _EMAIL.match(addr):
                    ok.append(addr)
                else:
                    refused.append(raw)
            elif _EMAIL.match(raw) and (self.external or self._belongs_to_org(raw)):
                ok.append(raw)
            elif raw:
                refused.append(raw)
        seen: set[str] = set()
        return [
            a for a in ok if not (a.lower() in seen or seen.add(a.lower()))
        ], refused

    def _belongs_to_org(self, address: str) -> bool:
        from sqlalchemy import func

        from app.db.models import AppRecord, OrganizationMembership, User

        org = self.app.organization_id
        if (
            self.db.query(User.id)
            .join(OrganizationMembership, OrganizationMembership.user_id == User.id)
            .filter(
                OrganizationMembership.organization_id == org,
                func.lower(User.email) == address.lower(),
            )
            .first()
        ):
            return True
        namespace = str(
            ((self.version.spec or {}).get("storage") or {}).get("namespace") or ""
        )
        if not namespace:
            return False
        return (
            self.db.query(AppRecord.id)
            .filter(
                AppRecord.namespace == namespace,
                AppRecord.organization_id == org,
                AppRecord.collection == "people",
                func.lower(AppRecord.data["email"].astext) == address.lower(),
            )
            .first()
            is not None
        )

    def _already_sent(self, key: str) -> bool:
        from app.db.models import EmailLog

        return (
            self.db.query(EmailLog.id)
            .filter(
                EmailLog.organization_id == self.app.organization_id,
                EmailLog.app_slug == self.app.slug,
                EmailLog.idempotency_key == str(key),
                EmailLog.status.in_(CLAIMED),
                EmailLog.is_test.is_(False),
            )
            .first()
            is not None
        )

    def _sent_today(self) -> int:
        from app.db.models import EmailLog

        since = datetime.now(timezone.utc) - timedelta(days=1)
        return (
            self.db.query(EmailLog)
            .filter(
                EmailLog.organization_id == self.app.organization_id,
                EmailLog.app_slug == self.app.slug,
                EmailLog.status.in_(CLAIMED),
                EmailLog.is_test.is_(False),
                EmailLog.created_at >= since,
            )
            .count()
        )

    def _release_stale_claim(self, session, key) -> None:
        """A send that crashed between claiming its key and recording the
        outcome leaves 'sending' behind; after STALE_CLAIM it no longer blocks
        (it's marked failed, which frees the key)."""
        from app.db.models import EmailLog

        cutoff = datetime.now(timezone.utc) - STALE_CLAIM
        stale = (
            session.query(EmailLog)
            .filter(
                EmailLog.organization_id == self.app.organization_id,
                EmailLog.app_slug == self.app.slug,
                EmailLog.idempotency_key == str(key),
                EmailLog.status == "sending",
                EmailLog.created_at < cutoff,
            )
            .all()
        )
        for row in stale:
            row.status = "failed"
            row.error_message = "the send was interrupted; the key was released"
        if stale:
            session.commit()

    def _tests_today_by_me(self) -> int:
        from app.db.models import EmailLog

        if not getattr(self.user, "id", None):
            return 0
        since = datetime.now(timezone.utc) - timedelta(days=1)
        return (
            self.db.query(EmailLog)
            .filter(
                EmailLog.organization_id == self.app.organization_id,
                EmailLog.app_slug == self.app.slug,
                EmailLog.sender_user_id == self.user.id,
                EmailLog.is_test.is_(True),
                EmailLog.created_at >= since,
            )
            .count()
        )

    def _sent_today_by_me(self) -> int:
        from app.db.models import EmailLog
        from app.services.app_runtime import is_system

        if is_system(self.user) or not getattr(self.user, "id", None):
            return 0
        since = datetime.now(timezone.utc) - timedelta(days=1)
        return (
            self.db.query(EmailLog)
            .filter(
                EmailLog.organization_id == self.app.organization_id,
                EmailLog.app_slug == self.app.slug,
                EmailLog.sender_user_id == self.user.id,
                EmailLog.status.in_(CLAIMED),
                EmailLog.is_test.is_(False),
                EmailLog.created_at >= since,
            )
            .count()
        )

    def _org_name(self) -> str:
        from app.db.models import Organization

        org = (
            self.db.query(Organization)
            .filter(Organization.id == self.app.organization_id)
            .first()
        )
        return (org.name if org else None) or "Norm"

    def _render(self, subject, html, heading=None, button=None) -> str:
        from app.services.email_templates import render_template

        btn = button if isinstance(button, dict) else {}
        _, body = render_template(
            "app_message",
            {
                "subject": subject,
                "heading": heading or subject,
                "body_html": str(html or ""),
                "button_label": btn.get("label"),
                "button_url": btn.get("url"),
                "org_name": self._org_name(),
                "app_name": self.app.name,
            },
        )
        return body

    def _log(self, session, to, cc, subject, html, *, key, kind, is_test, status):
        from app.config import settings
        from app.db.models import EmailLog

        log = EmailLog(
            organization_id=self.app.organization_id,
            sender_type="system",
            sender_email=settings.EMAIL_FROM_ADDRESS,
            sender_user_id=getattr(self.user, "id", None),
            to_addresses=to,
            cc_addresses=cc or None,
            subject=subject,
            template_name=f"app:{self.app.slug}:{kind or 'message'}",
            html_body=html,
            provider="resend",
            status=status,
            app_slug=self.app.slug,
            idempotency_key=str(key) if key else None,
            is_test=is_test,
        )
        session.add(log)
        return log

    def _deliver(
        self, to, cc, reply_to, subject, html
    ) -> tuple[str, str | None, str | None]:
        from app.config import settings

        if not settings.RESEND_API_KEY:
            return "failed", "RESEND_API_KEY not configured", None
        try:
            import resend

            resend.api_key = settings.RESEND_API_KEY
            payload = {
                "from": formataddr((self._org_name(), settings.EMAIL_FROM_ADDRESS)),
                "to": to,
                "subject": subject,
                "html": html,
            }
            if cc:
                payload["cc"] = cc
            if reply_to:
                payload["reply_to"] = reply_to
            result = resend.Emails.send(payload)
            provider_id = result.get("id") if isinstance(result, dict) else str(result)
            return "sent", None, provider_id
        except Exception as exc:  # noqa: BLE001 — a failed send is logged, not raised
            logger.warning("app email failed (%s): %s", self.app.slug, exc)
            return "failed", str(exc)[:500], None
