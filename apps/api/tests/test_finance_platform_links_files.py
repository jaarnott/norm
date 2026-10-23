"""The small platform pieces of Finance Phase 0: the shared public-link
derivation (Functions' links unchanged), the PDF check, the executor's binary
body, the validator's `requires` rules, the mode switch on entitlements, and
the venue listers that leave a non-trading venue out."""

import datetime as dt
import hashlib
import hmac
import io
import uuid
from types import SimpleNamespace

import pytest

from app.config import settings
from app.connectors.spec_executor import RenderedRequest, execute_spec
from app.db.functions_models import FnPublicToken
from app.db.models import Connection, Venue
from app.services import caller_scope, entitlements, public_links
from app.services.config_validator import check_endpoints_and_tools
from app.services.files.pdf_check import MAX_BYTES, check_pdf
from app.services.finance_core.errors import Invalid
from app.services.functions import public_tokens
from tests.conftest import _make_membership
from tests.org_helpers import grant_venue, make_org, make_user, make_venue

# ---------------------------------------------------------------------------
# public_links
# ---------------------------------------------------------------------------


class TestPublicLinks:
    def test_functions_links_are_byte_for_byte_what_they_were(self):
        """Functions' key, hash and token come from here now; a link already
        in a customer's inbox must keep resolving."""
        legacy_key = hmac.new(
            settings.JWT_SECRET.encode(), b"norm-functions/public-token/v1", "sha256"
        ).digest()
        assert public_links.key("functions") == legacy_key
        token = public_tokens._derive("quote", "row-1")
        assert token == public_links.derive("quote", "row-1", purpose="functions")
        assert (
            public_tokens.hash_token(token)
            == hashlib.sha256(token.encode()).hexdigest()
        )
        assert public_tokens.link_url("quote", token) == public_links.link_url(
            f"q/{token}"
        )
        assert public_tokens.link_url("portal", token).endswith(f"/e/{token}")

    def test_a_purpose_is_its_own_key(self):
        a = public_links.derive("portal", "row-1", purpose="functions")
        b = public_links.derive("portal", "row-1", purpose="entertainers")
        assert a != b and len(a) == len(b) == 43
        assert public_links.TOKEN_RE.fullmatch(a)
        assert public_links.matches(b, "row-1", "portal", purpose="entertainers")
        assert not public_links.matches(b, "row-1", "portal", purpose="functions")

    def test_link_url_is_the_app_url_plus_the_path(self):
        assert (
            public_links.link_url("/p/abc") == f"{settings.app_url.rstrip('/')}/p/abc"
        )
        assert public_links.link_url("join/abc").endswith("/join/abc")

    def test_resolve_fails_closed(self, db_session):
        from tests.functions_world import add_contact, add_enquiry, make_world

        fw = make_world(db_session)
        org = fw.org
        contact = add_contact(db_session, org.id)
        enquiry = add_enquiry(db_session, org.id, contact.id, fw.venue.id)
        now = dt.datetime.now(dt.timezone.utc)

        def row(kind="quote", **over):
            rid = str(uuid.uuid4())
            token = public_links.derive(kind, rid)
            db_session.add(
                FnPublicToken(
                    id=rid,
                    organization_id=org.id,
                    kind=kind,
                    # The Functions table insists a token points at a record.
                    enquiry_id=enquiry.id,
                    token_hash=public_links.hash_token(token),
                    expires_at=over.pop("expires_at", now + dt.timedelta(days=1)),
                    **over,
                )
            )
            db_session.flush()
            return rid, token

        rid, token = row()
        assert public_links.resolve(db_session, FnPublicToken, token).id == rid
        assert (
            public_links.resolve(db_session, FnPublicToken, token, kind="quote").id
            == rid
        )
        assert (
            public_links.resolve(db_session, FnPublicToken, token, kind="portal")
            is None
        )
        assert public_links.resolve(db_session, FnPublicToken, "not-a-token") is None
        assert public_links.resolve(db_session, FnPublicToken, None) is None
        assert public_links.resolve(db_session, FnPublicToken, "A" * 43) is None
        _, revoked = row(revoked_at=now)
        assert public_links.resolve(db_session, FnPublicToken, revoked) is None
        _, expired = row(expires_at=now - dt.timedelta(seconds=1))
        assert public_links.resolve(db_session, FnPublicToken, expired) is None
        _, open_ended = row(expires_at=None)
        assert public_links.resolve(db_session, FnPublicToken, open_ended) is not None


# ---------------------------------------------------------------------------
# pdf_check
# ---------------------------------------------------------------------------


def _pdf() -> bytes:
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


class TestPdfCheck:
    def test_a_plain_pdf_passes(self):
        data = _pdf()
        assert data.startswith(b"%PDF-")
        assert check_pdf(data) is None

    @pytest.mark.parametrize(
        "name", [b"/JavaScript", b"/JS", b"/OpenAction", b"/AcroForm"]
    )
    def test_active_content_is_refused(self, name):
        with pytest.raises(Invalid, match="scripts or a form"):
            check_pdf(_pdf() + b"\n1 0 obj << " + name + b" (x) >> endobj\n")

    def test_too_large_is_refused_before_parsing(self):
        with pytest.raises(Invalid, match="10 MB"):
            check_pdf(b"%PDF-1.4" + b"\0" * (MAX_BYTES - 8 + 1))

    def test_not_a_pdf(self):
        with pytest.raises(Invalid, match="isn't a PDF"):
            check_pdf(b"<html>hello</html>")
        with pytest.raises(Invalid, match="isn't a PDF"):
            check_pdf(b"\x89PNG\r\n\x1a\n")

    def test_empty_and_unparseable(self):
        with pytest.raises(Invalid, match="empty"):
            check_pdf(b"")
        with pytest.raises(Invalid, match="couldn't be read"):
            check_pdf(b"%PDF-1.4 nothing else here")


# ---------------------------------------------------------------------------
# spec_executor: an optional binary body
# ---------------------------------------------------------------------------


def _spec():
    return SimpleNamespace(
        execution_mode="template",
        base_url_template="https://files.example",
        auth_type="none",
        auth_config={},
        connector_name="files",
    )


class TestBinaryBody:
    def _run(self, monkeypatch, operation, fields, file_bytes=None):
        import app.connectors.spec_executor as mod

        sent = []

        def fake_http(rendered, op, **kw):
            sent.append(rendered)
            return SimpleNamespace(
                success=True,
                response_payload={"ok": True},
                error_message=None,
                reference=None,
            )

        monkeypatch.setattr(mod, "execute_http", fake_http)
        result, rendered = execute_spec(
            _spec(), operation, fields, {}, db=None, file_bytes=file_bytes
        )
        return result, rendered, sent

    def test_the_files_bytes_are_the_body_with_its_type(self, monkeypatch):
        files = {"f-1": (b"%PDF-1.4 ...", "application/pdf")}
        op = {
            "action": "attach",
            "method": "PUT",
            "path_template": "/attach/{{ invoice_id }}",
            "body_file_id": "attachment_file_id",
        }
        result, rendered, sent = self._run(
            monkeypatch,
            op,
            {"invoice_id": "inv-1", "attachment_file_id": "f-1"},
            file_bytes=lambda fid: files[fid],
        )
        assert result.success and sent[0] is rendered
        assert rendered.body == b"%PDF-1.4 ..." and rendered.method == "PUT"
        assert rendered.headers["Content-Type"] == "application/pdf"
        assert rendered.url.endswith("/attach/inv-1")
        audit = rendered.to_audit_dict()
        assert audit["body"] == {
            "binary": True,
            "size_bytes": len(b"%PDF-1.4 ..."),
            "content_type": "application/pdf",
        }

    def test_without_a_file_source_it_is_refused_not_sent_empty(self, monkeypatch):
        op = {
            "action": "attach",
            "method": "PUT",
            "path_template": "/a",
            "body_file_id": "file_id",
        }
        with pytest.raises(ValueError, match="file_bytes"):
            self._run(monkeypatch, op, {"file_id": "f-1"})
        with pytest.raises(ValueError, match="file_id is required"):
            self._run(monkeypatch, op, {}, file_bytes=lambda fid: (b"x", "text/plain"))

    def test_without_a_callback_the_finance_file_tables_serve_it(
        self, monkeypatch, db_session
    ):
        """A consolidator's call (page_writes → execute_consolidator → the
        HTTP path) passes no callback: the executor reads ent_files by id."""
        import hashlib

        import app.connectors.spec_executor as mod
        from app.db.entertainers_models import EntFile

        org = make_org(db_session, "Files Co")
        row = EntFile(
            organization_id=org.id,
            sha256=hashlib.sha256(b"%PDF-1.4 bill").hexdigest(),
            kind="invoice",
            filename="bill.pdf",
            content_type="application/pdf",
            size_bytes=len(b"%PDF-1.4 bill"),
            data=b"%PDF-1.4 bill",
        )
        db_session.add(row)
        db_session.flush()
        sent = []
        monkeypatch.setattr(
            mod,
            "execute_http",
            lambda rendered, op, **kw: (
                sent.append(rendered)
                or SimpleNamespace(
                    success=True,
                    response_payload={},
                    error_message=None,
                    reference=None,
                )
            ),
        )
        op = {
            "action": "attach",
            "method": "PUT",
            "path_template": "/a",
            "body_file_id": "file_id",
        }
        result, rendered = execute_spec(
            _spec(), op, {"file_id": row.id}, {}, db=db_session
        )
        assert result.success and rendered.body == b"%PDF-1.4 bill"
        assert rendered.headers["Content-Type"] == "application/pdf"
        with pytest.raises(ValueError, match="empty or missing"):
            execute_spec(_spec(), op, {"file_id": str(uuid.uuid4())}, {}, db=db_session)

    def test_rows_without_body_file_id_are_untouched(self, monkeypatch):
        op = {
            "action": "post",
            "method": "POST",
            "path_template": "/p",
            "request_body_template": '{"a": "{{ a }}"}',
        }
        result, rendered, sent = self._run(monkeypatch, op, {"a": "1"})
        assert rendered.body == {"a": "1"}
        assert rendered.to_audit_dict()["body"] == {"a": "1"}

    def test_a_bytes_body_goes_out_as_content(self, monkeypatch):
        """execute_http hands bytes to httpx as ``content`` (a str did; a
        dict is JSON)."""
        import app.connectors.spec_executor as mod

        seen = {}

        class _Client:
            def request(self, **kw):
                seen.update(kw)
                return SimpleNamespace(
                    status_code=200, headers={}, json=lambda: {}, text=""
                )

        monkeypatch.setattr(mod, "get_client", lambda: _Client(), raising=False)
        import app.connectors.http_pool as pool

        monkeypatch.setattr(pool, "get_client", lambda: _Client())
        mod.execute_http(
            RenderedRequest(method="PUT", url="https://x/y", headers={}, body=b"abc"),
            {"success_status_codes": [200]},
        )
        assert seen["content"] == b"abc" and seen["json"] is None


# ---------------------------------------------------------------------------
# config_validator: `requires`
# ---------------------------------------------------------------------------

CONS = {
    "action": "get_things",
    "consolidator_config": {
        "function_code": "def run(p, call_api, l):\n    return call_api('acme', 'get_raw', {})\n"
    },
}
RAW = {"action": "get_raw", "method": "GET", "path_template": "/r"}


def _problems(specs):
    return {(i.where, i.problem) for i in check_endpoints_and_tools(specs, {}, [])}


class TestValidatorRequires:
    def test_a_known_scope_list_passes(self):
        tool = {**CONS, "requires": ["payroll:read", "finance:read"]}
        assert _problems([("acme", "template", [tool], [RAW])]) == set()

    def test_an_unknown_scope_is_an_error(self):
        tool = {**CONS, "requires": ["payroll:read", "pay:everything"]}
        probs = _problems([("acme", "template", [tool], [RAW])])
        assert any(w == "acme.get_things" and "pay:everything" in p for w, p in probs)

    def test_a_platform_admin_scope_is_not_an_org_scope(self):
        tool = {**CONS, "requires": ["admin:system"]}
        assert any(
            "admin:system" in p
            for _, p in _problems([("acme", "template", [tool], [RAW])])
        )

    def test_a_bare_string_is_an_error(self):
        tool = {**CONS, "requires": "payroll:read"}
        assert any(
            "unknown permission" in p
            for _, p in _problems([("acme", "template", [tool], [RAW])])
        )

    def test_requires_on_an_endpoint_is_an_error(self):
        raw = {**RAW, "requires": ["payroll:read"]}
        probs = _problems([("acme", "template", [CONS], [raw])])
        assert any(
            w == "acme.get_raw" and "endpoint carrying `requires`" in p
            for w, p in probs
        )
        # Unsplit connectors are judged the same way: a row that IS an endpoint.
        probs = _problems([("acme", "template", [CONS, raw], None)])
        assert any(w == "acme.get_raw" and "endpoint carrying" in p for w, p in probs)


# ---------------------------------------------------------------------------
# entitlements: the module switches
# ---------------------------------------------------------------------------


class TestModeOffWins:
    def test_the_finance_catalog_decides_its_apps(self, db_session, monkeypatch):
        import app.services.finance_core.catalog as catalog

        seen = {}

        def entitled_after_modes(entitled, org_id, db):
            seen["args"] = (set(entitled), org_id)
            return entitled - {"loaded-payroll"}

        monkeypatch.setattr(catalog, "entitled_after_modes", entitled_after_modes)
        out = entitlements._mode_off_wins(
            {"xero", "loaded-payroll"}, "org-1", db_session
        )
        assert out == {"xero"}
        assert seen["args"] == ({"xero", "loaded-payroll"}, "org-1")

    def test_the_live_rule_drops_the_apps_while_their_modes_are_off(self, db_session):
        """The real finance_core.catalog (F1): loaded-payroll is off until
        payroll OR entertainers is on; norm-finance until finance is on."""
        org = make_org(db_session, "Modes Co")
        both = {"loaded-payroll", "norm-finance", "xero"}
        assert entitlements._mode_off_wins(set(both), org.id, db_session) == {"xero"}
        org.payroll_mode = "shadow"
        db_session.flush()
        assert entitlements._mode_off_wins(set(both), org.id, db_session) == {
            "loaded-payroll",
            "xero",
        }
        org.finance_mode = "shadow"
        db_session.flush()
        assert entitlements._mode_off_wins(set(both), org.id, db_session) == both

    def test_functions_off_still_wins(self, db_session, monkeypatch):
        from app.services.functions.catalog import CATALOG_SLUGS

        monkeypatch.setattr(
            "app.services.functions.entitlement.functions_mode", lambda db, org: "off"
        )
        slug = next(iter(CATALOG_SLUGS))
        assert entitlements._mode_off_wins({slug, "xero"}, "org-1", db_session) == {
            "xero"
        }
        assert entitlements._functions_off_wins is entitlements._mode_off_wins


# ---------------------------------------------------------------------------
# venue listers and a non-trading venue
# ---------------------------------------------------------------------------


@pytest.fixture()
def trading_world(db_session):
    org = make_org(db_session, "Cook Brothers")
    zeppa = make_venue(db_session, "La Zeppa", org)
    cbi = make_venue(db_session, "CBI Head Office", org)
    cbi.is_trading = False
    user = make_user(db_session)
    _make_membership(db_session, user, org)
    grant_venue(db_session, user, zeppa)
    grant_venue(db_session, user, cbi)
    for v in (zeppa, cbi):
        db_session.add(
            Connection(
                id=str(uuid.uuid4()),
                connector_name="loadedhub",
                venue_id=v.id,
                config={},
                enabled="true",
            )
        )
    db_session.flush()
    return SimpleNamespace(org=org, zeppa=zeppa, cbi=cbi, user=user)


def _names(payload):
    return [v["name"] for v in payload["venues"]]


class TestListVenuesFlags:
    def test_all_leaves_a_non_trading_venue_out_unless_asked(
        self, db_session, trading_world
    ):
        from app.agents.internal_tools import get_handler

        handler = get_handler("norm", "list_venues")
        with caller_scope.use(caller_scope.Scope(trading_world.org.id)):
            assert _names(handler({}, db_session, None)["data"]) == ["La Zeppa"]
            both = handler({"include_non_trading": "true"}, db_session, None)
            assert _names(both["data"]) == ["CBI Head Office", "La Zeppa"]

    def test_mine_is_the_persons_own_venues(self, db_session, trading_world):
        from app.agents.internal_tools import get_handler

        handler = get_handler("norm", "list_venues")
        other = make_venue(db_session, "Bessie", trading_world.org)
        assert other
        with caller_scope.use(caller_scope.for_user(db_session, trading_world.user)):
            assert _names(handler({}, db_session, None)["data"]) == [
                "Bessie",
                "La Zeppa",
            ]
            assert _names(handler({"mine": True}, db_session, None)["data"]) == [
                "La Zeppa"
            ]
        with caller_scope.use(caller_scope.Scope(trading_world.org.id)):
            out = handler({"mine": True}, db_session, None)
            assert out["success"] is False and "who is asking" in out["error"]


class TestNonTradingVenuesStayOut:
    def test_insights_never_run_for_it(self, db_session, trading_world):
        from app.services.insights.runner import _group_gaps, due_venues

        now = dt.datetime.now(dt.timezone.utc)
        due = {v.name for v, _ in due_venues(db_session, now, any_time=True)}
        assert "CBI Head Office" not in due
        assert (
            _group_gaps(db_session, trading_world.org.id, now) == 0
        )  # one trading venue

    def test_a_venue_less_dashboard_tile_skips_it(self, db_session, trading_world):
        from app.routers.reports_crud import _chart_venue_ids

        ids = _chart_venue_ids(db_session, trading_world.user, "loadedhub", None)
        assert ids == [trading_world.zeppa.id]
        # Asked for by id, it is still there for the payroll pages.
        assert _chart_venue_ids(
            db_session, trading_world.user, "loadedhub", trading_world.cbi.id
        ) == [trading_world.cbi.id]

    def test_mcp_does_not_offer_it(self, db_session, trading_world):
        from app.mcp.execution import VenueResolutionError, resolve_mcp_venue
        from app.mcp.principal import McpPrincipal

        principal = McpPrincipal(
            user_id=trading_world.user.id,
            organization_id=trading_world.org.id,
            venue_ids=(trading_world.zeppa.id, trading_world.cbi.id),
            scopes=frozenset({"mcp:reports:read"}),
        )
        assert resolve_mcp_venue(principal, None, db_session) == trading_world.zeppa.id
        with pytest.raises(VenueResolutionError):
            resolve_mcp_venue(principal, "CBI Head Office", db_session)

    def test_the_prompts_venue_list_and_suggestions_skip_it(
        self, db_session, trading_world
    ):
        from app.services.suggestions.engine import Context
        from app.services.venue_service import get_user_venues

        assert {v.name for v in get_user_venues(db_session, trading_world.user.id)} == {
            "CBI Head Office",
            "La Zeppa",
        }  # the raw list keeps it (GET /api/venues)
        ctx = Context(db_session, db_session, trading_world.user, None, None)
        assert [v.name for v in ctx.venues] == ["La Zeppa"]

    def test_get_api_venues_keeps_it(self, client, db_session, trading_world):
        from tests.org_helpers import auth_headers

        out = client.get("/api/venues", headers=auth_headers(trading_world.user))
        assert {v["name"] for v in out.json()["venues"]} == {
            "CBI Head Office",
            "La Zeppa",
        }
        assert db_session.query(Venue).filter(Venue.is_trading.is_(False)).count() == 1
