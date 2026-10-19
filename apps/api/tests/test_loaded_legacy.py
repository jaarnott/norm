"""Loaded Legacy (TEMPORARY — app/connectors/loaded_legacy.py) and the
general "password login" connector type it uses (services/password_login).

An ordinary connector (owner, 19 Oct 2026: "it should be the same as any other
connector"): a spec row, connected per venue through the standard connect card
with a Loaded username and password (the password encrypted like every typed
secret), the token from a password login. The owner's rules: keep the password
so Norm can log in again itself; admins, not users, fix a broken login. And
from Loaded's behaviour: each login can make that person log in to Loaded's
website again, so Norm logs in only when it must, and a refused login is never
retried in a loop (account lockout).
"""

import copy
import logging
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text

from app.connectors import loaded_legacy as LL
from app.connectors.base import ConnectorResult
from app.db.config_models import ConnectionSpec
from app.db.models import Connection, OrganizationMembership
from app.services import password_login as PL
from tests import consolidator_harness as harness
from tests.conftest import _make_venue
from tests.org_helpers import auth_headers, make_user, seed_system_roles
from tests.supplier_fakes import bind_sessions

PASSWORD = "s3cret-loaded-pw"


#: The companies Loaded's /api/user-session lists for the login.
COMPANIES = [
    {"id": "c-1", "name": "The Test Venue", "pos": "x"},
    {"id": "c-2", "name": "Another Co", "pos": "x"},
]


class FakeLoaded:
    """Loaded's token endpoint (numbered tokens, or a refusal) and the
    user-session call that lists the login's companies."""

    def __init__(self):
        self.logins = []
        self.refuse = False
        self.sessions = []

    def get(self, url, headers=None, **kw):
        self.sessions.append((url, (headers or {}).get("Authorization")))
        return httpx.Response(
            200,
            json={"userName": "chef@venue.com", "companies": COMPANIES},
            request=httpx.Request("GET", url),
        )

    def post(self, url, data=None, **kw):
        self.logins.append((url, dict(data or {})))
        req = httpx.Request("POST", url)
        if self.refuse:
            return httpx.Response(
                400,
                json={
                    "error": "invalid_grant",
                    "error_description": "The user name or password is incorrect.",
                },
                request=req,
            )
        return httpx.Response(
            200,
            json={"access_token": f"tok-{len(self.logins)}", "expires_in": 86400},
            request=req,
        )


@pytest.fixture()
def loaded(monkeypatch):
    fake = FakeLoaded()
    monkeypatch.setattr(PL.httpx, "post", fake.post)
    monkeypatch.setattr(PL.httpx, "get", fake.get)
    return fake


@pytest.fixture()
def emails(monkeypatch):
    sent = []
    monkeypatch.setattr(
        "app.services.email_service.send_system_email",
        lambda template, to, ctx, db, **k: sent.append((template, to, ctx)),
    )
    return sent


@pytest.fixture()
def spec(db_session):
    """The spec as sync_loaded_legacy_spec.py writes it: the tool's code too."""
    defined = copy.deepcopy(LL.SPEC)
    for tool in defined["tools"]:
        tool["consolidator_config"]["function_code"] = harness.source(tool["action"])
    row = ConnectionSpec(**defined)
    db_session.add(row)
    db_session.flush()
    return row


@pytest.fixture()
def venue_on_loaded(db_session, venue):
    """A venue connected to Loaded over OAuth (company c-1) — where recipes
    are read from."""
    db_session.add(
        Connection(
            connector_name="loadedhub",
            venue_id=venue.id,
            config={"x_loaded_company_id": "c-1"},
            enabled="true",
        )
    )
    db_session.flush()
    return venue


def _connect(client, headers, venue_id, password=PASSWORD, company="c-1"):
    config = {"username": "chef@venue.com", "password": password}
    if company is not None:
        config["x_loaded_company_id"] = company
    return client.put(
        "/api/connectors/loaded_legacy",
        json={
            "config": config,
            "venue_id": venue_id,
            "enabled": True,
        },
        headers=headers,
    )


@pytest.fixture()
def connected(client, admin_headers, spec, venue_on_loaded, loaded):
    r = _connect(client, admin_headers, venue_on_loaded.id)
    assert r.status_code == 200, r.text
    return venue_on_loaded


def _row(db, venue_id):
    db.expire_all()
    return (
        db.query(Connection)
        .filter_by(connector_name="loaded_legacy", venue_id=venue_id)
        .one()
    )


class TestConnecting:
    def test_the_standard_connect_checks_the_login_and_keeps_it_encrypted(
        self,
        admin_venue_access,
        client,
        admin_headers,
        db_session,
        spec,
        venue,
        loaded,
        monkeypatch,
    ):
        monkeypatch.setattr(
            "app.config.settings.TOKEN_ENCRYPTION_KEYS", Fernet.generate_key().decode()
        )
        # Nothing comes from the venue's LoadedHub connection (it has none).
        r = _connect(client, admin_headers, venue.id)
        assert r.status_code == 200 and PASSWORD not in r.text
        [(url, form)] = loaded.logins
        assert url == "https://loadedhub.com/api/Token"
        assert form["grant_type"] == "password" and form["client_id"] == "mercury"
        # The chosen company is checked against the login's, with its token.
        assert loaded.sessions == [
            ("https://loadedhub.com/api/user-session", "Bearer tok-1")
        ]
        row = _row(db_session, venue.id)
        assert row.config["x_loaded_company_id"] == "c-1"
        assert row.access_token == "tok-1"  # the check's token, no second login
        raw = db_session.execute(
            text(
                "SELECT config::text, access_token FROM connector_configs WHERE id = :i"
            ),
            {"i": row.id},
        ).one()
        assert PASSWORD not in raw[0] and "enc:v1:" in raw[0]
        assert raw[1].startswith("enc:v1:")
        # An expired token is only the next login, never "reconnect needed"
        # (there's no refresh token, which is what makes an OAuth row dead).
        row.token_expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
        db_session.flush()
        info = client.get(
            "/api/connectors/loaded_legacy/connect-info", headers=admin_headers
        ).json()
        assert info["auth_type"] == "password_login"
        [mine] = [v for v in info["venues"] if v["venue_id"] == venue.id]
        assert mine["status"] == "connected"
        # Both connect screens name the company the login works on.
        listed = client.get(
            f"/api/connectors?venue_id={venue.id}", headers=admin_headers
        ).json()["connectors"]
        [legacy] = [c for c in listed if c["name"] == "loaded_legacy"]
        assert legacy["needs_reconnect"] is False
        assert legacy["config"]["password"] == "••••••••"
        assert legacy["account"] == mine["account"] == "The Test Venue"
        assert legacy["account_choice"] == {
            "key": "x_loaded_company_id",
            "label": "Loaded company",
        }

    def test_a_refused_login_is_never_saved(
        self, client, admin_headers, db_session, spec, venue_on_loaded, loaded
    ):
        loaded.refuse = True
        r = _connect(client, admin_headers, venue_on_loaded.id, password="wrong")
        assert r.status_code == 400 and "incorrect" in r.json()["detail"]
        assert (
            not db_session.query(Connection)
            .filter_by(connector_name="loaded_legacy")
            .count()
        )

    def test_the_admin_picks_the_company_from_the_logins_own(
        self, client, admin_headers, db_session, spec, venue, loaded
    ):
        # No company yet: the login is checked, its companies come back to
        # pick from (by name; the one named like the venue suggested).
        r = _connect(client, admin_headers, venue.id, company=None)
        assert r.status_code == 409
        choose = r.json()["detail"]["choose"]
        assert [a["name"] for a in choose["accounts"]] == [
            "Another Co",
            "The Test Venue",
        ]
        assert choose["suggested"] == "c-1" and choose["key"] == "x_loaded_company_id"
        # One the login can't see is refused the same way.
        r = _connect(client, admin_headers, venue.id, company="c-9")
        assert r.status_code == 409 and "can't see" in r.json()["detail"]["message"]
        assert (
            not db_session.query(Connection)
            .filter_by(connector_name="loaded_legacy")
            .count()
        )

    def test_only_someone_at_the_venue_connects_it(
        self, client, db_session, organization, spec, venue_on_loaded, loaded
    ):
        roles = seed_system_roles(db_session)
        owner = make_user(db_session)
        db_session.add(
            OrganizationMembership(
                user_id=owner.id,
                organization_id=organization.id,
                role="owner",
                role_id=roles["owner"].id,
            )
        )
        db_session.flush()
        r = _connect(client, auth_headers(owner), venue_on_loaded.id)
        assert r.status_code == 403  # has the permission, not the venue


class TestPasswordLogin:
    def test_the_token_is_reused_until_it_nearly_expires(
        self, db_session, spec, connected, loaded
    ):
        assert PL.access_token(spec, db_session, connected.id) == "tok-1"
        assert len(loaded.logins) == 1  # the connect's check only
        row = _row(db_session, connected.id)
        row.token_expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
        db_session.flush()
        assert PL.access_token(spec, db_session, connected.id) == "tok-2"
        assert PL.access_token(spec, db_session, connected.id, force=True) == "tok-3"

    def test_a_refused_login_waits_an_hour_and_emails_once(
        self,
        admin_venue_access,
        client,
        admin_headers,
        db_session,
        spec,
        connected,
        loaded,
        emails,
    ):
        row = _row(db_session, connected.id)
        row.token_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db_session.flush()
        loaded.refuse = True
        with pytest.raises(PL.PasswordLoginError, match="incorrect"):
            PL.access_token(spec, db_session, connected.id)
        [(template, to, ctx)] = emails
        assert (
            template == "connector_login_failed" and ctx["connector"] == "Loaded Legacy"
        )
        assert PASSWORD not in str(ctx)
        info = client.get(
            "/api/connectors/loaded_legacy/connect-info", headers=admin_headers
        ).json()
        [mine] = [v for v in info["venues"] if v["venue_id"] == connected.id]
        assert (
            mine["status"] == "needs_reconnect"
            and "incorrect" in mine["last_auth_error"]
        )
        tries = len(loaded.logins)
        # Within the hour: no new attempt (repeated refusals lock accounts).
        for force in (False, True):
            with pytest.raises(PL.PasswordLoginError, match="tries again after"):
                PL.access_token(spec, db_session, connected.id, force=force)
        assert len(loaded.logins) == tries
        # An hour on it tries again; still refused, no second email.
        row = _row(db_session, connected.id)
        state = dict(row.oauth_metadata["password_login"])
        state["last_login_at"] = (
            datetime.now(timezone.utc) - timedelta(minutes=61)
        ).isoformat()
        row.oauth_metadata = {**row.oauth_metadata, "password_login": state}
        db_session.flush()
        with pytest.raises(PL.PasswordLoginError):
            PL.access_token(spec, db_session, connected.id)
        assert len(loaded.logins) == tries + 1 and len(emails) == 1
        # An admin enters a working login: fixed at once.
        loaded.refuse = False
        assert _connect(client, admin_headers, connected.id).status_code == 200
        row = _row(db_session, connected.id)
        assert row.needs_reconnect is False and row.last_auth_error is None

    def test_the_password_never_reaches_the_logs(
        self, client, admin_headers, db_session, spec, venue_on_loaded, loaded, caplog
    ):
        caplog.set_level(logging.DEBUG)
        _connect(client, admin_headers, venue_on_loaded.id)
        loaded.refuse = True
        _connect(
            client, admin_headers, venue_on_loaded.id, password="other-" + PASSWORD
        )
        with pytest.raises(PL.PasswordLoginError):
            PL.access_token(spec, db_session, venue_on_loaded.id, force=True)
        assert PASSWORD not in caplog.text


UNITS = [
    {"id": "u-g", "name": "Gram", "ratio": 0.001},
    {"id": "u-each", "name": "Each", "ratio": 1},
]


class FakeLoaded2:
    """Loaded at the HTTP layer, as Loaded Legacy reaches it: the older API's
    recipe and version reads, the version PUT, POS-item links, tenders."""

    def __init__(self, unauthorised=0):
        self.sent, self.unauthorised = [], unauthorised
        self.version = {
            "id": "v-1",
            "recipeId": "r-1",
            "yeildQuantity": 1.0,
            "yeildUnitId": "u-each",
            "yeildUnitName": "Each",
            "yeildUnitRatio": 1.0,
            "lines": [
                {
                    "id": "l-egg",
                    "stockItemId": "it-egg",
                    "stockItemName": "EGG YOLK",
                    "unitId": "u-each",
                    "unitName": "Each",
                    "unitRatio": 1.0,
                    "quantity": 12.0,
                }
            ],
        }

    def _ok(self, payload):
        return ConnectorResult(success=True, reference=None, response_payload=payload)

    def __call__(self, rendered, operation, **kw):
        self.sent.append(copy.deepcopy(rendered))
        url = rendered.url
        if rendered.method == "PUT":
            if self.unauthorised:
                self.unauthorised -= 1
                return ConnectorResult(
                    success=False,
                    reference=None,
                    response_payload={"status_code": 401, "body": ""},
                    error_message="API error 401",
                )
            self.version["lines"] = copy.deepcopy(rendered.body["lines"])
            return self._ok(None)
        if "/StockRecipeVersions/" in url:
            return self._ok(copy.deepcopy(self.version))
        if "/StockRecipes/" in url:
            return self._ok(
                {
                    "id": "r-1",
                    "name": "COMPONENT - AIOLI",
                    "notes": "",
                    "imagesJson": "[]",
                    "currentVersion": {"id": "v-1"},
                    "versions": [
                        {"id": "v-1", "versionNo": 1, "validFrom": None},
                        {
                            "id": "v-2",
                            "versionNo": 2,
                            "validFrom": "2027-01-01T00:00:00Z",
                        },
                    ],
                }
            )
        if "/StockPositemLinks" in url:
            return self._ok(
                [
                    {
                        "posIdentifier": "pos-1",
                        "recipeId": "r-1",
                        "stockDeleted": False,
                    },
                    {"posIdentifier": "pos-2", "recipeId": None, "stockItemId": "it-x"},
                    {
                        "posIdentifier": "pos-3",
                        "recipeId": "r-gone",
                        "stockDeleted": True,
                    },
                ]
            )
        if "/tenders/" in url and "/review" in url:
            return self._ok({"lines": [], "totalSpend": 0})
        if url.endswith("/tenders"):
            return self._ok([{"id": "t-1", "name": "Bidfood 2026"}])
        raise AssertionError(f"unexpected {rendered.method} {url}")

    def calls(self):
        return [
            (r.method, r.url.split("/r/")[-1].split("/internal/")[-1])
            for r in self.sent
        ]


@pytest.fixture()
def wapi(monkeypatch, db_session):
    bind_sessions(monkeypatch, db_session)
    fake = FakeLoaded2()
    monkeypatch.setattr("app.connectors.spec_executor.execute_http", fake)
    monkeypatch.setattr(
        "app.services.component_api.execute_component_action",
        lambda component, action, fields, venue_id, db, config_db: {"data": UNITS},
    )
    return fake


def _editor_recipe():
    """What the recipe editor sends: ids, DISPLAY quantities, full lines."""
    return {
        "recipe_id": "r-1",
        "version_id": "v-1",
        "lines": [
            {
                "kind": "item",
                "ref_id": "it-egg",
                "name": "EGG YOLK",
                "unit_id": "u-each",
                "quantity": 6,
            },
            {
                "kind": "item",
                "ref_id": "it-oil",
                "name": "OIL",
                "unit_id": "u-g",
                "quantity": 900,
            },
        ],
    }


class TestTheRecipeEditorSaves:
    """The editor and the claude.ai card save through record_recipe (one write
    path); end to end through the real executor, the tool's real code and
    Loaded Legacy's real endpoint rows."""

    def test_one_put_with_the_venues_company_and_its_own_token(
        self, db_session, spec, connected, wapi
    ):
        from app.services.recipe_save import save_recipe

        out = save_recipe(connected.id, _editor_recipe(), db_session, db_session)
        assert out["saved"] and out["detail"] == {"via": "loaded_legacy"}
        assert [m for m, _ in wapi.calls()] == ["GET", "GET", "PUT", "GET"]
        put = wapi.sent[2]
        assert put.url == (
            "https://loadedhub.com/wapi/r/StockRecipeVersions/v-1"
            "?validUntil=2027-01-01T00%3A00%3A00Z"
        )
        assert put.headers["Authorization"] == "Bearer tok-1"  # the connect's token
        assert put.headers["x-loaded-company-id"] == "c-1"  # the company picked
        egg, oil = put.body["lines"]
        assert egg["id"] == "l-egg" and egg["quantity"] == 6.0
        assert oil["quantity"] == 0.9 and oil["unitRatio"] == 0.001  # base units

    def test_loaded_answering_401_means_one_new_login_and_one_retry(
        self, db_session, spec, connected, wapi, loaded
    ):
        from app.services.recipe_save import save_recipe

        wapi.unauthorised = 1
        save_recipe(connected.id, _editor_recipe(), db_session, db_session)
        puts = [r for r in wapi.sent if r.method == "PUT"]
        assert [r.headers["Authorization"] for r in puts] == [
            "Bearer tok-1",
            "Bearer tok-2",
        ]

    def test_a_venue_without_loaded_legacy_is_told_to_connect_it(
        self, db_session, spec, connected, wapi
    ):
        from app.services.recipe_save import RecipeSaveError, save_recipe

        other = _make_venue(
            db_session, organization_id=connected.organization_id, name="Other"
        )
        with pytest.raises(RecipeSaveError, match="connect it"):
            save_recipe(other.id, _editor_recipe(), db_session, db_session)
        assert not [r for r in wapi.sent if r.method == "PUT"]

    def test_a_preview_never_logs_in(self, db_session, spec, connected, loaded):
        from app.connectors import spec_rows
        from app.connectors.spec_executor import render_request

        rendered = render_request(
            spec,
            spec_rows.find(spec, LL.SAVE_RECIPE_VERSION),
            {"version_id": "v-1", "body": {}},
            {"x_loaded_company_id": "c-1"},
            db=db_session,
            venue_id=connected.id,
            require_token=False,
        )
        assert rendered.url == "https://loadedhub.com/wapi/r/StockRecipeVersions/v-1"
        assert len(loaded.logins) == 1  # only the connect's check


class TestThePages:
    def test_the_tenders_page_reads_through_loaded_legacy(
        self, admin_venue_access, client, admin_headers, spec, connected, wapi, venue
    ):
        r = client.post(
            "/api/supplier-tenders/list",
            json={"venue_id": connected.id},
            headers=admin_headers,
        )
        assert r.status_code == 200 and r.json()["data"] == [
            {"id": "t-1", "name": "Bidfood 2026"}
        ]
        r = client.post(
            "/api/supplier-tenders/review",
            json={
                "venue_id": connected.id,
                "tender_id": "t-1",
                "start_time": "2026-10-01T00:00:00+13:00",
                "end_time": "2026-10-08T00:00:00+13:00",
            },
            headers=admin_headers,
        )
        assert r.status_code == 200
        review = wapi.sent[-1]
        assert review.url.startswith(
            "https://api.loadedhub.com/1.0/stock/internal/tenders/t-1/review?"
        )
        assert "startTime=2026-10-01T00%3A00%3A00%2B13%3A00" in review.url
        assert review.headers["x-loaded-company-id"] == "c-1"

    def test_the_tenders_page_asks_an_unconnected_venue_to_connect(
        self, admin_venue_access, client, admin_headers, spec, venue
    ):
        r = client.post(
            "/api/supplier-tenders/list",
            json={"venue_id": venue.id},
            headers=admin_headers,
        )
        assert r.status_code == 400 and "Loaded Legacy" in r.json()["detail"]

    def test_menu_engineering_joins_the_links_with_the_pos_item_names(
        self, db_session, spec, connected, wapi, monkeypatch
    ):
        from types import SimpleNamespace

        from app.services.menu_engineering import product_recipe_links

        monkeypatch.setattr(
            "app.connectors.tool_executor.execute_connector_tool",
            lambda connector, action, params, db, config_db, **kw: SimpleNamespace(
                success=True,
                payload=[
                    {"posIdentifier": "pos-1", "name": "Aioli Fries"},
                    {"posIdentifier": "pos-2", "name": "Coke"},
                    {"posIdentifier": "pos-3", "name": "Old Dish"},
                ],
            ),
        )
        links = product_recipe_links(
            connected.id, "2026-10-01", "2026-10-08", db_session, db_session
        )
        assert links == {
            "Aioli Fries": "r-1"
        }  # a stock-item link or a deleted one isn't a recipe
        other = _make_venue(
            db_session, organization_id=connected.organization_id, name="Other"
        )
        assert product_recipe_links(other.id, "a", "b", db_session, db_session) == {}
