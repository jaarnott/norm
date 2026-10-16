"""A Gmail mailbox played over ``httpx.MockTransport``, and a mailbox world.

``FakeGmail`` keeps messages, a history log, labels, drafts and send-as
settings, answers the Gmail REST endpoints the mailbox service uses, and
records every request. Failures are injected per route
(``fake.fail["GET /history"] = [429]``); history pages are small
(``page_size``) so pagination is exercised. No network, ever.
"""

from __future__ import annotations

import base64
import datetime as dt
import email
import json
import re
import threading
from email import policy
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import httpx

from app.db.functions_models import FnMailboxAccount
from app.db.models import Connection
from app.services.functions.mailbox import gmail_client
from app.services.functions.mailbox._sessions import independent_session
from tests.functions_world import make_world

UTC = dt.timezone.utc
MAILBOX = "events@brewbar.co.nz"

SYSTEM_LABELS = [
    {"id": "INBOX", "name": "INBOX", "type": "system"},
    {"id": "SENT", "name": "SENT", "type": "system"},
    {"id": "UNREAD", "name": "UNREAD", "type": "system"},
    {"id": "DRAFT", "name": "DRAFT", "type": "system"},
    {"id": "IMPORTANT", "name": "IMPORTANT", "type": "system"},
]
ACTIONED = [
    {"id": "Label_enq", "name": "Actioned - Enquiry", "type": "user"},
    {"id": "Label_res", "name": "Actioned - Reservation", "type": "user"},
    {"id": "Label_oth", "name": "Actioned - All Other Mail", "type": "user"},
]


def _b64(data: str) -> str:
    return base64.urlsafe_b64encode(data.encode()).decode().rstrip("=")


class FakeGmail:
    def __init__(self, address: str = MAILBOX):
        self.address = address
        self.history_id = 1000
        self.min_history = 1000
        self.messages: dict[str, dict] = {}
        self.history: list[dict] = []
        self.labels = SYSTEM_LABELS + ACTIONED
        self.drafts: dict[str, str] = {}  # draft id -> message id
        self.send_as = [
            {
                "sendAsEmail": address,
                "displayName": "Brew Bar Events",
                "isPrimary": True,
                "signature": "<div>Brew Bar · 1 Quay St</div><script>x()</script>",
            }
        ]
        self.requests: list[httpx.Request] = []
        self.fail: dict[str, list] = {}
        self.page_size = 2
        self.list_page_size = 3
        self.sent: list[dict] = []
        self.rewrite_message_id = False
        self.lose_send_response = False
        #: Who Google's userinfo says signed in (default: the mailbox).
        self.userinfo_email: str | None = None
        self.attachment_data: dict[tuple[str, str], bytes] = {}
        #: Real Gmail may hand out a new attachment id on each fetch.
        self.rotate_attachment_ids = False
        #: advances by one per request — tests use it as the sync's clock
        self.clock = 0.0
        self.on_request = None
        self._n = 0
        self._lock = threading.Lock()

    # -- building the mailbox ------------------------------------------------
    def _next(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}{self._n:04d}"

    def _record(self, **change) -> None:
        self.history_id += 1
        self.history.append({"id": str(self.history_id), **change})

    def add_message(
        self,
        *,
        frm: str,
        to: list[str] | str,
        subject: str = "Wedding enquiry",
        body: str = "Hello there",
        html: str | None = None,
        cc: list[str] | None = None,
        thread_id: str | None = None,
        labels: list[str] | None = None,
        when: dt.datetime | None = None,
        headers: dict | None = None,
        message_id: str | None = None,
        reply_to: str | None = None,
        record: bool = True,
        attachments: list[dict] | None = None,
    ) -> str:
        mid = self._next("m")
        tid = thread_id or self._next("t")
        when = when or dt.datetime.now(UTC)
        to_list = [to] if isinstance(to, str) else list(to)
        hdrs = [
            {"name": "From", "value": frm},
            {"name": "To", "value": ", ".join(to_list)},
            {"name": "Subject", "value": subject},
            {"name": "Date", "value": when.strftime("%a, %d %b %Y %H:%M:%S +0000")},
            {"name": "Message-ID", "value": message_id or f"<{mid}@mail.example>"},
        ]
        if cc:
            hdrs.append({"name": "Cc", "value": ", ".join(cc)})
        if reply_to:
            hdrs.append({"name": "Reply-To", "value": reply_to})
        for name, value in (headers or {}).items():
            hdrs.append({"name": name, "value": value})
        parts = [{"mimeType": "text/plain", "body": {"data": _b64(body)}}]
        if html is not None:
            parts.append({"mimeType": "text/html", "body": {"data": _b64(html)}})
        for i, att in enumerate(attachments or []):
            aid = f"ATT{mid}x{i}"
            self.attachment_data[(mid, aid)] = att["data"]
            parts.append(
                {
                    "partId": str(len(parts)),
                    "mimeType": att.get("mime_type", "application/pdf"),
                    "filename": att["filename"],
                    "body": {
                        "attachmentId": aid,
                        "size": att.get("size", len(att["data"])),
                    },
                }
            )
        self.messages[mid] = {
            "id": mid,
            "threadId": tid,
            "labelIds": list(labels if labels is not None else ["INBOX", "UNREAD"]),
            "snippet": body[:100],
            "internalDate": str(int(when.timestamp() * 1000)),
            "payload": {
                "mimeType": "multipart/mixed"
                if attachments
                else "multipart/alternative",
                "headers": hdrs,
                "parts": parts,
            },
        }
        if record:
            self._record(
                messagesAdded=[
                    {
                        "message": {
                            "id": mid,
                            "threadId": tid,
                            "labelIds": self.messages[mid]["labelIds"],
                        }
                    }
                ]
            )
        return mid

    def delete_message(self, mid: str) -> None:
        msg = self.messages.pop(mid)
        self._record(
            messagesDeleted=[{"message": {"id": mid, "threadId": msg["threadId"]}}]
        )

    def relabel(self, mid: str, add=(), remove=()) -> None:
        msg = self.messages[mid]
        msg["labelIds"] = [x for x in msg["labelIds"] if x not in set(remove)] + [
            x for x in add if x not in msg["labelIds"]
        ]
        if add:
            self._record(
                labelsAdded=[
                    {
                        "message": {"id": mid, "threadId": msg["threadId"]},
                        "labelIds": list(add),
                    }
                ]
            )
        if remove:
            self._record(
                labelsRemoved=[
                    {
                        "message": {"id": mid, "threadId": msg["threadId"]},
                        "labelIds": list(remove),
                    }
                ]
            )

    def expire_history(self) -> None:
        """Gmail only keeps history so long: older cursors now 404."""
        self.min_history = self.history_id

    # -- projections ---------------------------------------------------------
    def _resource(self, mid: str, fmt: str, wanted: list[str] | None = None) -> dict:
        msg = self.messages[mid]
        out = {k: v for k, v in msg.items() if k != "payload"}
        if fmt == "minimal":
            return out
        payload = msg["payload"]
        if fmt == "metadata":
            headers = payload["headers"]
            if wanted:
                low = {w.lower() for w in wanted}
                headers = [h for h in headers if h["name"].lower() in low]
            out["payload"] = {"mimeType": payload["mimeType"], "headers": headers}
            return out
        if self.rotate_attachment_ids:
            payload = json.loads(json.dumps(payload))
            for part in payload.get("parts") or []:
                aid = (part.get("body") or {}).get("attachmentId")
                if aid:
                    self._n += 1
                    fresh = f"{aid}r{self._n}"
                    self.attachment_data[(mid, fresh)] = self.attachment_data[
                        (mid, aid)
                    ]
                    part["body"]["attachmentId"] = fresh
        out["payload"] = payload
        return out

    def _store_raw(self, raw: str, thread_id: str | None, labels: list[str]) -> dict:
        data = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
        parsed = email.message_from_bytes(data, policy=policy.default)
        html = text = ""
        for part in parsed.walk():
            if part.get_content_type() == "text/html":
                html = part.get_content()
            elif part.get_content_type() == "text/plain" and not text:
                text = part.get_content()
        headers = {k: str(v) for k, v in parsed.items()}
        if self.rewrite_message_id:
            headers["Message-ID"] = f"<rewritten.{self._n}@mail.gmail.com>"
        mid = self.add_message(
            frm=headers.get("From", self.address),
            to=headers.get("To", ""),
            cc=[headers["Cc"]] if headers.get("Cc") else None,
            subject=headers.get("Subject", ""),
            body=text,
            html=html,
            thread_id=thread_id,
            labels=labels,
            message_id=headers.get("Message-ID"),
            headers={
                k: v
                for k, v in headers.items()
                if k in ("In-Reply-To", "References", "Bcc")
            },
        )
        self.messages[mid]["raw_headers"] = headers
        return self.messages[mid]

    def _messages_list(self, q: str) -> list[str]:
        ids = sorted(
            (
                m
                for m in self.messages.values()
                if not {"DRAFT", "TRASH", "SPAM"} & set(m["labelIds"])
            ),
            key=lambda m: int(m["internalDate"]),
            reverse=True,
        )
        window = re.search(r"newer_than:(\d+)d", q or "")
        if window:
            since = dt.datetime.now(UTC) - dt.timedelta(days=int(window.group(1)))
            ids = [m for m in ids if int(m["internalDate"]) >= since.timestamp() * 1000]
        found = re.search(r"rfc822msgid:(\S+)", q or "")
        if found:
            want = found.group(1).strip("<>")
            return [
                m["id"]
                for m in ids
                if any(
                    h["name"] == "Message-ID" and h["value"].strip("<>") == want
                    for h in m["payload"]["headers"]
                )
            ]
        if "in:sent" in (q or ""):
            return [m["id"] for m in ids if "SENT" in m["labelIds"]]
        return [m["id"] for m in ids]

    # -- the transport ---------------------------------------------------------
    def _respond(self, status: int, body=None) -> httpx.Response:
        if body is None:
            return httpx.Response(status)
        return httpx.Response(status, json=body)

    def _error(self, status: int, reason: str = "backendError") -> httpx.Response:
        return httpx.Response(
            status,
            json={
                "error": {
                    "code": status,
                    "message": f"fake {status}",
                    "errors": [{"reason": reason}],
                }
            },
        )

    def __call__(self, request: httpx.Request) -> httpx.Response:
        with self._lock:
            return self._handle(request)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        self.clock += 1
        if self.on_request is not None:
            self.on_request(request)
        url = urlparse(str(request.url))
        path = url.path.split("/gmail/v1/users/me", 1)[-1]
        params = parse_qs(url.query)
        method = request.method
        generic = re.sub(r"/(m|t|d)\d+", "/{id}", path)
        route = f"{method} {generic}"
        queue = self.fail.get(route) or self.fail.get(f"{method} {path}")
        failure = queue.pop(0) if queue else None
        if failure is not None:
            if isinstance(failure, Exception):
                raise failure
            status, reason = (
                failure if isinstance(failure, tuple) else (failure, "backendError")
            )
            return self._error(status, reason)
        body = json.loads(request.content) if request.content else {}

        if url.path == "/oauth2/v2/userinfo":
            return self._respond(200, {"email": self.userinfo_email or self.address})
        if path == "/profile":
            return self._respond(
                200, {"emailAddress": self.address, "historyId": str(self.history_id)}
            )
        if path == "/history":
            start = int(params["startHistoryId"][0])
            if start < self.min_history:
                return self._error(404, "notFound")
            records = [h for h in self.history if int(h["id"]) > start]
            offset = int(params.get("pageToken", ["0"])[0])
            page = records[offset : offset + self.page_size]
            out = {"history": page, "historyId": str(self.history_id)}
            if offset + self.page_size < len(records):
                out["nextPageToken"] = str(offset + self.page_size)
            return self._respond(200, out)
        if path == "/messages" and method == "GET":
            ids = self._messages_list(params.get("q", [""])[0])
            offset = int(params.get("pageToken", ["0"])[0])
            size = min(self.list_page_size, int(params.get("maxResults", ["500"])[0]))
            page = ids[offset : offset + size]
            out = {
                "messages": [
                    {"id": i, "threadId": self.messages[i]["threadId"]} for i in page
                ]
            }
            if offset + size < len(ids):
                out["nextPageToken"] = str(offset + size)
            return self._respond(200, out)
        if "/attachments/" in path and method == "GET":
            _, _, mid, _, aid = path.split("/")
            data = self.attachment_data.get((mid, aid))
            if data is None:
                return self._error(404, "notFound")
            return self._respond(
                200,
                {
                    "size": len(data),
                    "data": base64.urlsafe_b64encode(data).decode().rstrip("="),
                },
            )
        if path.startswith("/messages/") and method == "GET":
            mid = path.split("/")[2]
            if mid not in self.messages:
                return self._error(404, "notFound")
            fmt = params.get("format", ["full"])[0]
            return self._respond(
                200, self._resource(mid, fmt, params.get("metadataHeaders"))
            )
        if path == "/messages/send":
            msg = self._store_raw(body["raw"], body.get("threadId"), ["SENT"])
            self.sent.append(
                {"raw": body["raw"], "threadId": body.get("threadId"), "id": msg["id"]}
            )
            if self.lose_send_response:
                # Gmail sent it; the answer never arrived.
                raise httpx.ReadTimeout("timed out", request=request)
            return self._respond(
                200,
                {"id": msg["id"], "threadId": msg["threadId"], "labelIds": ["SENT"]},
            )
        if path.startswith("/threads/") and path.endswith("/modify"):
            tid = path.split("/")[2]
            msgs = [m for m in self.messages.values() if m["threadId"] == tid]
            if not msgs:
                return self._error(404, "notFound")
            for m in msgs:
                self.relabel(
                    m["id"],
                    add=body.get("addLabelIds", []),
                    remove=body.get("removeLabelIds", []),
                )
            return self._respond(200, {"id": tid})
        if path.startswith("/threads/"):
            tid = path.split("/")[2]
            msgs = sorted(
                (m for m in self.messages.values() if m["threadId"] == tid),
                key=lambda m: int(m["internalDate"]),
            )
            if not msgs:
                return self._error(404, "notFound")
            fmt = params.get("format", ["full"])[0]
            return self._respond(
                200,
                {
                    "id": tid,
                    "messages": [
                        self._resource(m["id"], fmt, params.get("metadataHeaders"))
                        for m in msgs
                    ],
                },
            )
        if path == "/labels":
            return self._respond(200, {"labels": self.labels})
        if path == "/settings/sendAs":
            return self._respond(200, {"sendAs": self.send_as})
        if path == "/drafts" and method == "POST":
            message = body["message"]
            msg = self._store_raw(message["raw"], message.get("threadId"), ["DRAFT"])
            did = self._next("d")
            self.drafts[did] = msg["id"]
            return self._respond(
                200,
                {"id": did, "message": {"id": msg["id"], "threadId": msg["threadId"]}},
            )
        if path.startswith("/drafts/"):
            did = path.split("/")[2]
            if did not in self.drafts:
                return self._error(404, "notFound")
            if method == "DELETE":
                mid = self.drafts.pop(did)
                self.delete_message(mid)
                return httpx.Response(204)
            fmt = params.get("format", ["full"])[0]
            return self._respond(
                200, {"id": did, "message": self._resource(self.drafts[did], fmt)}
            )
        return self._error(404, "notFound")

    # -- helpers for tests --------------------------------------------------------
    def send_draft_from_gmail(self, did: str) -> str:
        """A person pressing Send on a Norm draft in Gmail."""
        mid = self.drafts.pop(did)
        draft = self.messages[mid]
        self.delete_message(mid)
        hdrs = {h["name"]: h["value"] for h in draft["payload"]["headers"]}
        html = next(
            (
                base64.urlsafe_b64decode(p["body"]["data"] + "==").decode()
                for p in draft["payload"]["parts"]
                if p["mimeType"] == "text/html"
            ),
            "",
        )
        return self.add_message(
            frm=hdrs["From"],
            to=hdrs["To"],
            subject=hdrs["Subject"],
            body="sent",
            html=html,
            thread_id=draft["threadId"],
            labels=["SENT"],
        )

    def sent_message(self, index: int = -1) -> email.message.EmailMessage:
        raw = self.sent[index]["raw"]
        data = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
        return email.message_from_bytes(data, policy=policy.default)

    def calls(self, method: str, fragment: str) -> list[httpx.Request]:
        return [
            r for r in self.requests if r.method == method and fragment in str(r.url)
        ]


class FakeTokens:
    def __init__(self, token: str = "tok", dead: str | None = None):
        self.token = token
        self.dead = dead
        self.calls = 0
        self.forced = 0

    def access_token(self, *, force_refresh: bool = False) -> str:
        self.calls += 1
        if force_refresh:
            self.forced += 1
        if self.dead:
            raise gmail_client.MailboxAuthError(self.dead)
        return self.token


def install(
    monkeypatch, fake: FakeGmail, tokens: FakeTokens | None = None
) -> FakeTokens:
    """Route every Gmail call to ``fake`` and every token to ``tokens``."""
    tokens = tokens or FakeTokens()
    monkeypatch.setattr(gmail_client, "TRANSPORT", httpx.MockTransport(fake))
    monkeypatch.setattr(gmail_client, "_verified", {})  # identity checks start cold
    monkeypatch.setattr(
        gmail_client,
        "tokens_for",
        lambda connection_id, db=None, config_db=None: tokens,
    )
    return tokens


def sessions(db):
    """The sync's session factory under the test harness (savepoints)."""
    return lambda: independent_session(db)


def mailbox_world(
    db, *, mode: str = "live", history_id: int | None = 1000, status="active"
):
    """The Functions world plus Brew Bar's connected venue mailbox."""
    w = make_world(db, mode=mode)
    conn = Connection(
        connector_name="venue_mailbox",
        venue_id=w.venue.id,
        config={},
        enabled="true",
        access_token="tok",
        refresh_token="ref",
        token_expires_at=dt.datetime.now(UTC) + dt.timedelta(hours=1),
    )
    db.add(conn)
    db.flush()
    account = FnMailboxAccount(
        organization_id=w.org.id,
        venue_id=w.venue.id,
        google_email=MAILBOX,
        connection_id=conn.id,
        status=status,
        history_id=history_id,
        last_ok_at=dt.datetime.now(UTC),
    )
    db.add(account)
    db.flush()
    w.conn = conn
    w.account = account
    w.staff.email = "sam@cookbros.co.nz"
    db.flush()
    return w


def other_mailbox(db, w):
    """A connected mailbox in the rival organisation."""
    conn = Connection(
        connector_name="venue_mailbox",
        venue_id=w.other_venue.id,
        config={},
        enabled="true",
        access_token="tok2",
        refresh_token="ref2",
    )
    db.add(conn)
    db.flush()
    account = FnMailboxAccount(
        organization_id=w.other_org.id,
        venue_id=w.other_venue.id,
        google_email="hello@rivalbar.co.nz",
        connection_id=conn.id,
        status="active",
        history_id=1000,
    )
    db.add(account)
    db.flush()
    return SimpleNamespace(conn=conn, account=account)
