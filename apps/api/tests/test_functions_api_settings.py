"""The Norm Functions staff API — uploads, the venue profile (areas,
settings, brand, image library), templates and proposals.

Beyond the happy paths, the rules worth pinning here:

* Uploads are judged by their BYTES: an SVG or a script named ``.png`` is
  refused, the same picture twice is one file, another org's file is a 404.
* Who approves — and whether an area needs approval at all — can't be
  changed by any staff member, or approval would be a formality.
* Proposal HTML is stored sanitised; the money check flags a figure that
  matches nothing on record.
* Cloning a Loaded menu goes through Norm's own tool (faked here: no network
  in tests), and a Loaded outage is a clear refusal, not a 500.
"""

from __future__ import annotations

import io
import time

import pytest
from PIL import Image

from app.db.functions_models import FnFile, FnPreorderMenu
from app.services.functions import proposals as proposals_svc
from app.services.functions import reports
from tests.conftest import _make_membership, _make_user
from tests.functions_world import make_world
from tests.test_functions_api import H, call, detail_of, new_enquiry, new_option


@pytest.fixture
def w(db_session):
    return make_world(db_session)


def png(colour=(200, 30, 30), size=(40, 20)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, colour).save(buf, format="PNG")
    return buf.getvalue()


def upload(client, user, data: bytes, name="photo.png"):
    return client.post(
        "/api/functions/files",
        headers=H(user),
        files={"file": (name, data, "image/png")},
    )


# ===========================================================================
# Uploads
# ===========================================================================


class TestUploads:
    def test_upload_sniffs_dedupes_and_serves(self, client, w):
        r = upload(client, w.staff, png())
        assert r.status_code == 201, r.text
        f = r.json()
        assert f["content_type"] == "image/png" and (f["width"], f["height"]) == (
            40,
            20,
        )
        again = upload(client, w.staff, png(), name="copy.png")
        assert again.json()["id"] == f["id"]  # content-addressed
        got = client.get(f"/api/functions/files/{f['id']}", headers=H(w.staff))
        assert got.status_code == 200
        assert got.headers["content-type"] == "image/png"
        assert got.headers["x-content-type-options"] == "nosniff"
        assert got.content == png()

    def test_non_images_are_refused_whatever_they_claim(self, client, w):
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
        assert upload(client, w.staff, svg, name="logo.png").status_code == 422
        assert upload(client, w.staff, b"<html>hi</html>").status_code == 422
        assert upload(client, w.staff, b"").status_code == 422
        truncated = png()[:60]
        assert upload(client, w.staff, truncated).status_code == 422
        buf = io.BytesIO()
        Image.new("RGB", (10, 10)).save(buf, format="BMP")
        r = upload(client, w.staff, buf.getvalue(), name="x.bmp")
        assert r.status_code == 422 and "BMP" in r.json()["detail"]["message"]

    def test_files_are_per_organisation(self, client, w):
        f = upload(client, w.staff, png()).json()
        assert (
            client.get(
                f"/api/functions/files/{f['id']}", headers=H(w.other_user)
            ).status_code
            == 404
        )
        theirs = upload(client, w.other_user, png())
        assert theirs.status_code == 201 and theirs.json()["id"] != f["id"]
        assert w.db.query(FnFile).filter(FnFile.sha256 == f["sha256"]).count() == 2

    def test_shadow_refuses_uploads(self, client, w):
        w.org.functions_mode = "shadow"
        w.db.flush()
        r = upload(client, w.staff, png())
        assert r.status_code == 409 and r.json()["detail"]["code"] == "not_live"


# ===========================================================================
# Venue profile
# ===========================================================================


class TestVenueProfile:
    def test_profile_in_one_call(self, client, w):
        r = call(client, "GET", f"/venues/{w.venue.id}/profile", w.staff)
        assert r.status_code == 200, r.text
        p = r.json()
        assert {a["name"] for a in p["areas"]} == {"Deck", "Snug"}
        assert p["settings"]["approver_name"] == "Andy Approver"
        assert p["can_govern_approvals"] is False
        assert p["brand"] is None and p["images"] == []
        assert (
            call(client, "GET", f"/venues/{w.venue2.id}/profile", w.staff).status_code
            == 404
        )
        assert (
            call(
                client, "GET", f"/venues/{w.venue.id}/profile", w.other_user
            ).status_code
            == 404
        )

    def test_areas_crud_and_the_approval_flag(self, client, w):
        r = call(
            client,
            "POST",
            f"/venues/{w.venue.id}/areas",
            w.staff,
            json={
                "name": "Loft",
                "capacity_seated": 40,
                "booking_types": ["large_event_partial"],
            },
        )
        assert r.status_code == 201, r.text
        loft = r.json()
        assert loft["requires_approval"] is True
        no_approval = call(
            client,
            "POST",
            f"/venues/{w.venue.id}/areas",
            w.staff,
            json={"name": "Bar", "requires_approval": False},
        )
        assert no_approval.status_code == 403
        flip = call(
            client,
            "PATCH",
            f"/areas/{loft['id']}",
            w.staff,
            json={"requires_approval": False},
        )
        assert flip.status_code == 403
        # A null is never read as "no approval needed".
        assert (
            call(
                client,
                "PATCH",
                f"/areas/{loft['id']}",
                w.approver,
                json={"requires_approval": None},
            ).status_code
            == 422
        )
        ok = call(
            client,
            "PATCH",
            f"/areas/{loft['id']}",
            w.approver,
            json={"version": loft["version"], "requires_approval": False},
        )
        assert ok.status_code == 200 and ok.json()["requires_approval"] is False
        assert (
            call(
                client,
                "PATCH",
                f"/areas/{loft['id']}",
                w.staff,
                json={"version": loft["version"], "name": "x"},
            ).status_code
            == 409
        )
        renamed = call(
            client,
            "PATCH",
            f"/areas/{loft['id']}",
            w.staff,
            json={"name": "The Loft", "booking_types": ["seated_dining"]},
        )
        assert renamed.json()["name"] == "The Loft"
        assert (
            call(
                client,
                "PATCH",
                f"/areas/{loft['id']}",
                w.staff,
                json={"booking_types": ["rave"]},
            ).status_code
            == 422
        )
        assert call(client, "DELETE", f"/areas/{loft['id']}", w.staff).json() == {
            "result": "deleted"
        }
        enquiry_id = detail_of(new_enquiry(client, w))
        new_option(client, w, enquiry_id)  # on the Deck
        assert call(client, "DELETE", f"/areas/{w.deck.id}", w.staff).json() == {
            "result": "retired"
        }

    def test_area_photos(self, client, w):
        a = upload(client, w.staff, png()).json()
        b = upload(client, w.staff, png((0, 0, 255))).json()
        p1 = call(
            client,
            "POST",
            f"/areas/{w.deck.id}/photos",
            w.staff,
            json={"file_id": a["id"], "caption": "Sunset"},
        ).json()
        p2 = call(
            client,
            "POST",
            f"/areas/{w.deck.id}/photos",
            w.staff,
            json={"file_id": b["id"]},
        ).json()
        assert (p1["sort_order"], p2["sort_order"]) == (0, 1)
        r = call(
            client,
            "PUT",
            f"/areas/{w.deck.id}/photos/order",
            w.staff,
            json={"photo_ids": [p2["id"], p1["id"]]},
        )
        assert [p["id"] for p in r.json()["rows"]] == [p2["id"], p1["id"]]
        assert (
            call(
                client,
                "PUT",
                f"/areas/{w.deck.id}/photos/order",
                w.staff,
                json={"photo_ids": [p1["id"]]},
            ).status_code
            == 422
        )
        assert (
            call(
                client,
                "PATCH",
                f"/area-photos/{p1['id']}",
                w.staff,
                json={"caption": "Dusk"},
            ).json()["caption"]
            == "Dusk"
        )
        profile = call(client, "GET", f"/venues/{w.venue.id}/profile", w.staff).json()
        deck = next(x for x in profile["areas"] if x["id"] == w.deck.id)
        assert [p["id"] for p in deck["photos"]] == [p2["id"], p1["id"]]
        assert (
            call(client, "DELETE", f"/area-photos/{p1['id']}", w.staff).status_code
            == 204
        )
        rival_file = upload(client, w.other_user, png((1, 2, 3))).json()
        assert (
            call(
                client,
                "POST",
                f"/areas/{w.deck.id}/photos",
                w.staff,
                json={"file_id": rival_file["id"]},
            ).status_code
            == 422
        )
        assert (
            call(
                client,
                "POST",
                f"/areas/{w.deck.id}/photos",
                w.other_user,
                json={"file_id": rival_file["id"]},
            ).status_code
            == 404
        )

    def test_settings_and_who_may_change_the_approver(self, client, w):
        p = call(client, "GET", f"/venues/{w.venue.id}/profile", w.staff).json()
        version = p["settings"]["version"]
        r = call(
            client,
            "PUT",
            f"/venues/{w.venue.id}/settings",
            w.staff,
            json={
                "version": version,
                "policies": "No confetti.",
                "menu_links": [
                    {"label": "Drinks", "url": "https://brew.bar/drinks.pdf"}
                ],
                "enabled_fields": {"seated_dining": {"details.min_spend": False}},
                "notify_emails": ["Events@Brew.Bar", "events@brew.bar"],
            },
        )
        assert r.status_code == 200, r.text
        s = r.json()["settings"]
        assert s["policies"] == "No confetti."
        assert s["notify_emails"] == ["events@brew.bar"]
        assert s["menu_links"][0]["active"] is True
        assert r.json()["enabled_fields"]["seated_dining"]["details.min_spend"] is False

        assert (
            call(
                client,
                "PUT",
                f"/venues/{w.venue.id}/settings",
                w.staff,
                json={"approver_user_id": w.staff.id},
            ).status_code
            == 403
        )
        assert (
            call(
                client,
                "PUT",
                f"/venues/{w.venue.id}/settings",
                w.staff,
                json={"menu_links": [{"label": "x", "url": "javascript:alert(1)"}]},
            ).status_code
            == 422
        )
        assert (
            call(
                client,
                "PUT",
                f"/venues/{w.venue.id}/settings",
                w.staff,
                json={"enabled_fields": {"seated_dining": {"details.colour": True}}},
            ).status_code
            == 422
        )
        assert (
            call(
                client,
                "PUT",
                f"/venues/{w.venue.id}/settings",
                w.staff,
                json={"notify_emails": ["not-an-email"]},
            ).status_code
            == 422
        )

        no_access = _make_user(w.db, full_name="Nell Noaccess")
        _make_membership(w.db, no_access, w.org, role="member")
        r = call(
            client,
            "PUT",
            f"/venues/{w.venue.id}/settings",
            w.owner,
            json={"approver_user_id": no_access.id},
        )
        assert r.status_code == 422 and "access" in r.json()["detail"]["message"]
        r = call(
            client,
            "PUT",
            f"/venues/{w.venue.id}/settings",
            w.owner,
            json={"approver_user_id": w.other_user.id},
        )
        assert r.status_code == 422
        r = call(
            client,
            "PUT",
            f"/venues/{w.venue.id}/settings",
            w.owner,
            json={"approver_user_id": w.staff.id},
        )
        assert (
            r.status_code == 200
            and r.json()["settings"]["approver_name"] == "Sam Staff"
        )
        # The new approver now approves; the old one doesn't.
        enquiry_id = detail_of(new_enquiry(client, w))
        option = new_option(client, w, enquiry_id)
        assert (
            call(
                client, "POST", f"/min-spends/{option['id']}/approve", w.approver
            ).status_code
            == 403
        )
        assert (
            call(
                client, "POST", f"/min-spends/{option['id']}/approve", w.staff
            ).status_code
            == 200
        )

    def test_settings_created_for_a_venue_without_them(self, client, w):
        r = call(
            client,
            "PUT",
            f"/venues/{w.venue2.id}/settings",
            w.owner,
            json={"policies": "Garden rules"},
        )
        assert r.status_code == 200 and r.json()["settings"]["version"] == 1
        # A venue in the path the caller can't use is not found, like any id.
        assert (
            call(
                client,
                "PUT",
                f"/venues/{w.venue2.id}/settings",
                w.staff,
                json={"policies": "x"},
            ).status_code
            == 404
        )

    def test_brand(self, client, w):
        logo = upload(client, w.staff, png()).json()
        r = call(
            client,
            "PUT",
            f"/venues/{w.venue.id}/brand",
            w.staff,
            json={
                "wordmark": "Brew Bar",
                "logo_file_id": logo["id"],
                "colours": {"primary": "#1f2a44", "accent": "#b08d57"},
                "fonts": {"heading": "Playfair Display"},
                "terms": [
                    "Deposit secures the date.",
                    {"title": "Cancellations", "body": "14 days' notice."},
                ],
                "inclusions": ["Dedicated host", ""],
                "signoff": {"title": "Let's lock it in", "contact": "events@brew.bar"},
                "valid_days": 21,
                "template_html": '<h1 onclick="steal()">{{event_name}}</h1><script>alert(1)</script>{{options}}',
            },
        )
        assert r.status_code == 200, r.text
        brand = r.json()
        assert brand["version"] == 1 and brand["inclusions"] == ["Dedicated host"]
        assert brand["terms"][1] == {
            "title": "Cancellations",
            "body": "14 days' notice.",
        }
        assert (
            "<script" not in brand["template_html"]
            and "onclick" not in brand["template_html"]
        )
        assert "{{event_name}}" in brand["template_html"]
        assert (
            call(
                client,
                "PUT",
                f"/venues/{w.venue.id}/brand",
                w.staff,
                json={"version": 1, "colours": {"primary": "red;}"}},
            ).status_code
            == 422
        )
        assert (
            call(
                client,
                "PUT",
                f"/venues/{w.venue.id}/brand",
                w.staff,
                json={"version": 1, "valid_days": 0},
            ).status_code
            == 422
        )
        rival_logo = upload(client, w.other_user, png((9, 9, 9))).json()
        assert (
            call(
                client,
                "PUT",
                f"/venues/{w.venue.id}/brand",
                w.staff,
                json={"logo_file_id": rival_logo["id"]},
            ).status_code
            == 422
        )
        assert (
            call(
                client,
                "PUT",
                f"/venues/{w.venue.id}/brand",
                w.staff,
                json={"version": 0, "tagline": "x"},
            ).status_code
            == 409
        )
        assert (
            call(
                client,
                "PUT",
                f"/venues/{w.venue.id}/brand",
                w.other_user,
                json={"tagline": "x"},
            ).status_code
            == 404
        )
        # Reset: back to Norm's defaults; the logo file itself stays.
        assert (
            call(
                client, "DELETE", f"/venues/{w.venue.id}/brand", w.other_user
            ).status_code
            == 404
        )
        assert (
            call(client, "DELETE", f"/venues/{w.venue.id}/brand", w.staff).status_code
            == 204
        )
        profile = call(client, "GET", f"/venues/{w.venue.id}/profile", w.staff).json()
        assert profile["brand"] is None
        assert call(client, "GET", f"/files/{logo['id']}", w.staff).status_code == 200
        fresh = call(
            client,
            "PUT",
            f"/venues/{w.venue.id}/brand",
            w.staff,
            json={"tagline": "New"},
        ).json()
        assert fresh["version"] == 1 and fresh["tagline"] == "New"

    def test_image_library(self, client, w):
        r = client.post(
            f"/api/functions/venues/{w.venue.id}/images/upload",
            headers=H(w.staff),
            files={"file": ("deck-at-dusk.png", png(), "image/png")},
            data={"caption": "Deck"},
        )
        assert r.status_code == 201, r.text
        image = r.json()
        assert image["name"] == "deck-at-dusk" and image["caption"] == "Deck"
        renamed = call(
            client,
            "PATCH",
            f"/images/{image['id']}",
            w.staff,
            json={
                "version": image["version"],
                "name": "Deck dusk",
                "tags": ["Outdoor", "outdoor"],
            },
        )
        assert renamed.json()["name"] == "Deck dusk" and renamed.json()["tags"] == [
            "outdoor"
        ]
        f = upload(client, w.staff, png((5, 5, 5))).json()
        added = call(
            client,
            "POST",
            f"/venues/{w.venue.id}/images",
            w.staff,
            json={"file_id": f["id"], "name": "Snug"},
        )
        assert added.status_code == 201
        assert (
            len(
                call(client, "GET", f"/venues/{w.venue.id}/images", w.staff).json()[
                    "rows"
                ]
            )
            == 2
        )
        assert (
            call(
                client,
                "PATCH",
                f"/images/{image['id']}",
                w.other_user,
                json={"name": "x"},
            ).status_code
            == 404
        )
        assert (
            call(client, "DELETE", f"/images/{image['id']}", w.staff).status_code == 204
        )
        assert (
            call(client, "GET", f"/files/{image['file_id']}", w.staff).status_code
            == 200
        )  # files stay


# ===========================================================================
# Templates
# ===========================================================================

SET_MENU_ITEMS = [
    {"kind": "header", "name": "Mains"},
    {
        "name": "Fish",
        "default_percent": 50,
        "unit_price_cents": 3500,
        "dietaries": ["gf"],
        "loaded_recipe_id": "r-1",
    },
    {"name": "Beef", "default_percent": 40, "loaded_stock_item_id": "s-1"},
]


class TestTemplates:
    def test_preorder_template_crud(self, client, w):
        r = call(
            client,
            "POST",
            "/preorder-templates",
            w.staff,
            json={
                "venue_id": w.venue.id,
                "name": "Set dinner",
                "is_set_menu": True,
                "price_per_person_cents": 7500,
                "items": SET_MENU_ITEMS,
            },
        )
        assert r.status_code == 201, r.text
        t = r.json()
        fish = t["items"][1]
        assert fish["unit_price_cents"] is None  # set-menu dishes are unpriced
        assert fish["dietaries"] == ["GF"]
        assert any("add up to 90" in x for x in t["warnings"])
        assert not any("not linked" in x for x in t["warnings"])
        r = call(
            client,
            "PATCH",
            f"/preorder-templates/{t['id']}",
            w.staff,
            json={
                "version": t["version"],
                "items": SET_MENU_ITEMS + [{"name": "Tofu", "default_percent": 10}],
            },
        )
        assert r.status_code == 200, r.text
        assert len(r.json()["items"]) == 4 and r.json()["version"] == t["version"] + 1
        assert any("not linked" in x for x in r.json()["warnings"])
        assert (
            call(
                client,
                "PATCH",
                f"/preorder-templates/{t['id']}",
                w.staff,
                json={"version": t["version"], "name": "x"},
            ).status_code
            == 409
        )
        assert (
            call(
                client,
                "PATCH",
                f"/preorder-templates/{t['id']}",
                w.staff,
                json={"items": [{"kind": "starter", "name": "x"}]},
            ).status_code
            == 422
        )
        listed = call(
            client, "GET", f"/preorder-templates?venue_id={w.venue.id}", w.staff
        ).json()["rows"]
        assert [x["name"] for x in listed] == ["Set dinner"]

        enquiry_id = detail_of(new_enquiry(client, w))
        call(
            client,
            "POST",
            f"/enquiries/{enquiry_id}/menus",
            w.staff,
            json={"template_id": t["id"]},
        )
        assert call(
            client, "DELETE", f"/preorder-templates/{t['id']}", w.staff
        ).json() == {"result": "retired"}
        assert call(client, "GET", "/preorder-templates", w.staff).json()["rows"] == []
        assert (
            len(
                call(
                    client, "GET", "/preorder-templates?include_inactive=true", w.staff
                ).json()["rows"]
            )
            == 1
        )
        assert w.db.query(FnPreorderMenu).filter_by(template_id=t["id"]).count() == 1

        spare = call(
            client,
            "POST",
            "/preorder-templates",
            w.staff,
            json={"venue_id": w.venue.id, "name": "Spare"},
        ).json()
        assert call(
            client, "DELETE", f"/preorder-templates/{spare['id']}", w.staff
        ).json() == {"result": "deleted"}

    def test_templates_respect_venues_and_organisations(self, client, w):
        assert (
            call(
                client,
                "POST",
                "/preorder-templates",
                w.staff,
                json={"venue_id": w.venue2.id, "name": "x"},
            ).status_code
            == 403
        )
        t = call(
            client,
            "POST",
            "/preorder-templates",
            w.owner,
            json={"venue_id": w.venue2.id, "name": "Garden"},
        ).json()
        assert (
            call(client, "GET", f"/preorder-templates/{t['id']}", w.staff).status_code
            == 404
        )
        assert (
            call(
                client, "GET", f"/preorder-templates/{t['id']}", w.other_user
            ).status_code
            == 404
        )
        assert (
            call(
                client,
                "PATCH",
                f"/preorder-templates/{t['id']}",
                w.other_user,
                json={"name": "x"},
            ).status_code
            == 404
        )
        assert (
            call(
                client, "DELETE", f"/preorder-templates/{t['id']}", w.other_user
            ).status_code
            == 404
        )

    def test_clone_a_loaded_menu(self, client, w, monkeypatch):
        calls = []

        def fake(db, config_db, venue, action, params):
            calls.append((venue.name, action, params))
            if "menu_id" not in params:
                return {
                    "menus": [
                        {
                            "id": "m-1",
                            "name": "Spring Canapés",
                            "sections": 2,
                            "lines": 3,
                        }
                    ]
                }, None
            return {
                "id": "m-1",
                "name": "Spring Canapés",
                "detail": "full",
                "groups": [
                    {
                        "name": "Cold",
                        "groupOrder": 2,
                        "lines": [
                            {
                                "name": "Oysters",
                                "workingPrice": 6.5,
                                "stockItemId": "si-9",
                                "lineOrder": 1,
                            }
                        ],
                    },
                    {
                        "name": "Hot",
                        "groupOrder": 1,
                        "lines": [
                            {
                                "name": "Sliders",
                                "workingPrice": 9,
                                "recipeId": "rc-2",
                                "lineOrder": 2,
                            },
                            {
                                "name": "Arancini",
                                "workingPrice": 7.25,
                                "recipeId": "rc-1",
                                "lineOrder": 1,
                            },
                            {"name": "Gone", "deletedAt": "2026-01-01", "lineOrder": 3},
                        ],
                    },
                ],
            }, None

        monkeypatch.setattr(reports, "loaded_call", fake)
        listed = call(
            client, "GET", f"/venues/{w.venue.id}/loaded-menus", w.staff
        ).json()
        assert listed == {
            "available": True,
            "menus": [
                {
                    "id": "m-1",
                    "name": "Spring Canapés",
                    "sections": 2,
                    "lines": 3,
                    "created": None,
                }
            ],
            "reason": None,
        }
        r = call(
            client,
            "POST",
            "/preorder-templates/clone-loaded",
            w.staff,
            json={"venue_id": w.venue.id, "loaded_menu_id": "m-1"},
        )
        assert r.status_code == 201, r.text
        t = r.json()
        assert t["name"] == "Spring Canapés" and t["loaded_menu_id"] == "m-1"
        assert [(i["kind"], i["name"]) for i in t["items"]] == [
            ("header", "Hot"),
            ("item", "Arancini"),
            ("item", "Sliders"),
            ("header", "Cold"),
            ("item", "Oysters"),
        ]
        arancini = t["items"][1]
        assert (
            arancini["unit_price_cents"] == 725
            and arancini["loaded_recipe_id"] == "rc-1"
        )
        assert t["items"][4]["loaded_stock_item_id"] == "si-9"
        assert calls[-1] == (
            "Brew Bar",
            "get_menus",
            {"menu_id": "m-1", "detail": "full"},
        )

        monkeypatch.setattr(
            reports,
            "loaded_call",
            lambda *a, **k: (None, "LoadedHub isn't reachable right now."),
        )
        down = call(
            client,
            "POST",
            "/preorder-templates/clone-loaded",
            w.staff,
            json={"venue_id": w.venue.id, "loaded_menu_id": "m-1"},
        )
        assert (
            down.status_code == 422 and "reachable" in down.json()["detail"]["message"]
        )
        assert (
            call(client, "GET", f"/venues/{w.venue.id}/loaded-menus", w.staff).json()[
                "available"
            ]
            is False
        )
        assert (
            call(
                client, "GET", f"/venues/{w.venue2.id}/loaded-menus", w.staff
            ).status_code
            == 404
        )
        assert (
            call(
                client,
                "POST",
                "/preorder-templates/clone-loaded",
                w.other_user,
                json={"venue_id": w.venue.id, "loaded_menu_id": "m-1"},
            ).status_code
            == 404
        )

    def test_loaded_recipe_search(self, client, w, monkeypatch):
        monkeypatch.setattr(
            reports,
            "loaded_call",
            lambda *a, **k: (
                {"query": "fish", "matches": [{"id": "r1", "name": "Fish pie"}]},
                None,
            ),
        )
        r = call(
            client, "GET", f"/venues/{w.venue.id}/loaded-recipes?q=fish", w.staff
        ).json()
        assert r == {
            "available": True,
            "recipes": [{"id": "r1", "name": "Fish pie"}],
            "reason": None,
        }
        assert (
            call(
                client, "GET", f"/venues/{w.venue.id}/loaded-recipes?q=f", w.staff
            ).json()["recipes"]
            == []
        )

    def test_reply_templates(self, client, w):
        body = {
            "venue_id": w.venue.id,
            "booking_type": "seated_dining",
            "stage": "first_reply",
            "name": "Dining hello",
            "subject": "Your booking",
            "body_html": "<p>Hi {{client_name}}</p>",
        }
        r = call(client, "POST", "/reply-templates", w.staff, json=body)
        assert r.status_code == 201, r.text
        t = r.json()
        assert (
            call(
                client,
                "POST",
                "/reply-templates",
                w.staff,
                json={**body, "venue_id": None},
            ).status_code
            == 403  # an all-venue template shows at venues the author can't see
        )
        group = call(
            client,
            "POST",
            "/reply-templates",
            w.owner,
            json={**body, "venue_id": None, "booking_type": None, "name": "Everyone"},
        )
        assert group.status_code == 201
        assert (
            call(
                client,
                "POST",
                "/reply-templates",
                w.staff,
                json={**body, "stage": "nudge"},
            ).status_code
            == 422
        )
        listed = call(
            client,
            "GET",
            f"/reply-templates?venue_id={w.venue.id}&booking_type=seated_dining&stage=first_reply",
            w.staff,
        ).json()["rows"]
        assert {x["name"] for x in listed} == {"Dining hello", "Everyone"}
        assert {
            x["name"]
            for x in call(
                client, "GET", "/reply-templates?booking_type=group_booking", w.staff
            ).json()["rows"]
        } == {"Everyone"}
        r = call(
            client,
            "PATCH",
            f"/reply-templates/{t['id']}",
            w.staff,
            json={"version": t["version"], "active": False},
        )
        assert r.json()["active"] is False
        assert (
            call(
                client,
                "PATCH",
                f"/reply-templates/{group.json()['id']}",
                w.staff,
                json={"name": "x"},
            ).status_code
            == 403
        )
        assert (
            call(
                client,
                "PATCH",
                f"/reply-templates/{t['id']}",
                w.other_user,
                json={"name": "x"},
            ).status_code
            == 404
        )
        assert (
            call(client, "DELETE", f"/reply-templates/{t['id']}", w.staff).status_code
            == 204
        )
        assert (
            call(client, "GET", "/reply-templates", w.other_user).json()["rows"] == []
        )


# ===========================================================================
# Proposals
# ===========================================================================


class TestProposals:
    def _setup(self, client, w):
        call(
            client,
            "PUT",
            f"/venues/{w.venue.id}/brand",
            w.staff,
            json={
                "wordmark": "Brew Bar",
                "inclusions": ["Host"],
                "terms": ["Deposit 20%"],
                "valid_days": 21,
            },
        )
        enquiry_id = detail_of(new_enquiry(client, w))
        option = new_option(client, w, enquiry_id, label="The Deck", amount=550_000)
        return enquiry_id, option

    def test_start_from_the_brand_save_sanitised_and_check(self, client, w):
        enquiry_id, option = self._setup(client, w)
        empty = call(client, "GET", f"/enquiries/{enquiry_id}/proposal", w.staff).json()
        assert empty["proposal"] is None and empty["summary"]["exists"] is False
        assert empty["options"][0]["min_spend_id"] == option["id"]

        r = call(client, "POST", f"/enquiries/{enquiry_id}/proposal", w.staff, json={})
        assert r.status_code == 200, r.text
        p = r.json()["proposal"]
        assert (
            "Brew Bar" in p["html"] and "$5,500" in p["html"] and "Casey" in p["html"]
        )
        assert "valid for 21 days" in p["html"]
        assert p["based_on_brand_version"] == 1
        check = r.json()["check"]
        assert check["unmatched_cents"] == []
        assert any(
            "isn't approved" in x for x in check["warnings"]
        )  # Deck needs approval

        again = call(
            client, "POST", f"/enquiries/{enquiry_id}/proposal", w.staff, json={}
        )
        assert again.json()["proposal"]["id"] == p["id"]  # starting twice returns it

        saved = call(
            client,
            "PUT",
            f"/enquiries/{enquiry_id}/proposal",
            w.staff,
            json={
                "version": p["version"],
                "html": '<p onmouseover="x()">Your total is $5,500 or $6,100</p><script>bad()</script><img src="https://evil.example/x.png">',
            },
        )
        assert saved.status_code == 200, saved.text
        html = saved.json()["proposal"]["html"]
        assert (
            "<script" not in html
            and "onmouseover" not in html
            and "evil.example" not in html
        )
        assert saved.json()["check"]["unmatched_cents"] == [610_000]
        assert any("$6,100" in x for x in saved.json()["check"]["warnings"])
        stale = call(
            client,
            "PUT",
            f"/enquiries/{enquiry_id}/proposal",
            w.staff,
            json={"version": p["version"], "html": "<p>x</p>"},
        )
        assert stale.status_code == 409

        draft = call(
            client,
            "POST",
            f"/enquiries/{enquiry_id}/proposal/check",
            w.staff,
            json={
                "html": "<b>$5,</b>500 is the minimum, $5,550 rounds, $6,100, not $65pp"
            },
        )
        # Tags don't split a figure; a dollar either way matches; punctuation
        # after a figure doesn't hide it; per-person prices aren't judged.
        assert draft.json()["stated_cents"] == [550_000, 555_000, 610_000]
        assert draft.json()["unmatched_cents"] == [555_000, 610_000]

        preview = call(
            client, "GET", f"/enquiries/{enquiry_id}/proposal/preview", w.staff
        ).json()["document"]
        assert (
            preview.startswith("<!doctype html>")
            and "Content-Security-Policy" in preview
        )

    def test_restart_and_versions(self, client, w):
        enquiry_id, option = self._setup(client, w)
        p = call(
            client, "POST", f"/enquiries/{enquiry_id}/proposal", w.staff, json={}
        ).json()["proposal"]
        call(
            client,
            "PUT",
            f"/enquiries/{enquiry_id}/proposal",
            w.staff,
            json={"html": "<p>mine</p>"},
        )
        current = call(
            client, "GET", f"/enquiries/{enquiry_id}/proposal", w.staff
        ).json()["proposal"]
        assert (
            call(
                client,
                "POST",
                f"/enquiries/{enquiry_id}/proposal",
                w.staff,
                json={"restart": True, "version": p["version"]},
            ).status_code
            == 409
        )
        restarted = call(
            client,
            "POST",
            f"/enquiries/{enquiry_id}/proposal",
            w.staff,
            json={"restart": True, "version": current["version"]},
        )
        assert "$5,500" in restarted.json()["proposal"]["html"]

        from app.services.functions.ctx import build_ctx

        ctx = build_ctx(w.db, w.staff)
        version = proposals_svc.freeze_version(w.db, ctx, enquiry_id)
        proposals_svc.mark_version_sent(w.db, ctx, version.id, outbox_id=None)
        w.db.flush()
        rows = call(
            client, "GET", f"/enquiries/{enquiry_id}/proposal/versions", w.staff
        ).json()["rows"]
        assert [r["version_number"] for r in rows] == [1]
        assert rows[0]["options"][0]["amount_cents"] == 550_000 and rows[0]["sent_at"]
        full = call(client, "GET", f"/proposal-versions/{version.id}", w.staff).json()
        assert "$5,500" in full["html"]
        detail = call(client, "GET", f"/enquiries/{enquiry_id}", w.staff).json()
        assert detail["stage"] == "quoted" and detail["proposal"]["last_sent_at"]
        assert any(i["kind"] == "proposal_sent" for i in detail["timeline"])
        assert (
            call(
                client,
                "GET",
                f"/enquiries/{enquiry_id}/proposal/preview?version_id={version.id}",
                w.staff,
            ).status_code
            == 200
        )
        assert (
            call(
                client, "GET", f"/proposal-versions/{version.id}", w.other_user
            ).status_code
            == 404
        )

    def test_brand_template_tokens(self, client, w):
        call(
            client,
            "PUT",
            f"/venues/{w.venue.id}/brand",
            w.staff,
            json={
                "template_html": "<h1>{{ event_name }} for {{client_name}}</h1>{{options}}<p>{{unknown}}</p>",
                "template_css": "h1 { color: #123456; }",
            },
        )
        enquiry_id = detail_of(new_enquiry(client, w))
        new_option(client, w, enquiry_id, amount=300_000)
        p = call(
            client, "POST", f"/enquiries/{enquiry_id}/proposal", w.staff, json={}
        ).json()["proposal"]
        assert (
            "Casey&#x27;s 40th for Casey Client" in p["html"]
            or "Casey's 40th for Casey Client" in p["html"]
        )
        assert "$3,000" in p["html"] and "{{unknown}}" in p["html"]
        assert "#123456" in p["css"]

    def test_proposals_are_tenant_and_mode_guarded(self, client, w):
        enquiry_id, _ = self._setup(client, w)
        call(client, "POST", f"/enquiries/{enquiry_id}/proposal", w.staff, json={})
        rival = w.other_user
        for method, path, body in (
            ("GET", f"/enquiries/{enquiry_id}/proposal", None),
            ("POST", f"/enquiries/{enquiry_id}/proposal", {}),
            ("PUT", f"/enquiries/{enquiry_id}/proposal", {"html": "x"}),
            ("POST", f"/enquiries/{enquiry_id}/proposal/check", {}),
            ("GET", f"/enquiries/{enquiry_id}/proposal/preview", None),
            ("GET", f"/enquiries/{enquiry_id}/proposal/versions", None),
        ):
            assert call(client, method, path, rival, json=body).status_code == 404, path
        w.org.functions_mode = "shadow"
        w.db.flush()
        r = call(
            client,
            "PUT",
            f"/enquiries/{enquiry_id}/proposal",
            w.staff,
            json={"html": "<p>x</p>"},
        )
        assert r.status_code == 409 and r.json()["detail"]["code"] == "not_live"
        assert (
            call(
                client, "GET", f"/enquiries/{enquiry_id}/proposal", w.staff
            ).status_code
            == 200
        )


# ===========================================================================
# Isolation and shadow mode for the settings group
# ===========================================================================


class TestSettingsIsolation:
    def test_another_organisation_gets_404_on_every_settings_route(self, client, w):
        f = upload(client, w.staff, png()).json()
        photo = call(
            client,
            "POST",
            f"/areas/{w.deck.id}/photos",
            w.staff,
            json={"file_id": f["id"]},
        ).json()
        image = call(
            client,
            "POST",
            f"/venues/{w.venue.id}/images",
            w.staff,
            json={"file_id": f["id"]},
        ).json()
        rival = w.other_user
        rival_file = upload(client, rival, png((7, 7, 7))).json()
        for method, path, body in (
            ("GET", f"/venues/{w.venue.id}/profile", None),
            ("PUT", f"/venues/{w.venue.id}/settings", {"policies": "x"}),
            ("POST", f"/venues/{w.venue.id}/areas", {"name": "x"}),
            ("PATCH", f"/areas/{w.deck.id}", {"name": "x"}),
            ("DELETE", f"/areas/{w.deck.id}", None),
            ("POST", f"/areas/{w.deck.id}/photos", {"file_id": rival_file["id"]}),
            ("PUT", f"/areas/{w.deck.id}/photos/order", {"photo_ids": [photo["id"]]}),
            ("PATCH", f"/area-photos/{photo['id']}", {"caption": "x"}),
            ("DELETE", f"/area-photos/{photo['id']}", None),
            ("PUT", f"/venues/{w.venue.id}/brand", {"tagline": "x"}),
            ("GET", f"/venues/{w.venue.id}/images", None),
            ("POST", f"/venues/{w.venue.id}/images", {"file_id": rival_file["id"]}),
            ("PATCH", f"/images/{image['id']}", {"name": "x"}),
            ("DELETE", f"/images/{image['id']}", None),
            ("GET", f"/venues/{w.venue.id}/loaded-menus", None),
            ("GET", f"/venues/{w.venue.id}/loaded-recipes?q=fish", None),
            ("GET", f"/venues/{w.venue.id}/sales-reference?date=2026-12-04", None),
            ("GET", f"/files/{f['id']}", None),
        ):
            r = call(client, method, path, rival, json=body)
            assert r.status_code == 404, (method, path, r.status_code, r.text)
        r = client.post(
            f"/api/functions/venues/{w.venue.id}/images/upload",
            headers=H(rival),
            files={"file": ("x.png", png((8, 8, 8)), "image/png")},
        )
        assert r.status_code == 404

    def test_shadow_mode_refuses_settings_writes(self, client, w):
        template = call(
            client,
            "POST",
            "/preorder-templates",
            w.staff,
            json={"venue_id": w.venue.id, "name": "T"},
        ).json()
        w.org.functions_mode = "shadow"
        w.db.flush()
        assert (
            call(client, "GET", f"/venues/{w.venue.id}/profile", w.staff).status_code
            == 200
        )
        assert call(client, "GET", "/preorder-templates", w.staff).status_code == 200
        for method, path, body in (
            ("PUT", f"/venues/{w.venue.id}/settings", {"policies": "x"}),
            ("POST", f"/venues/{w.venue.id}/areas", {"name": "x"}),
            ("PATCH", f"/areas/{w.deck.id}", {"name": "x"}),
            ("PUT", f"/venues/{w.venue.id}/brand", {"tagline": "x"}),
            ("POST", "/preorder-templates", {"venue_id": w.venue.id, "name": "x"}),
            ("PATCH", f"/preorder-templates/{template['id']}", {"name": "x"}),
            ("DELETE", f"/preorder-templates/{template['id']}", None),
            (
                "POST",
                "/reply-templates",
                {"venue_id": w.venue.id, "stage": "follow_up", "name": "x"},
            ),
            ("POST", "/contacts/x/anonymise", {"confirm": True}),
        ):
            r = call(client, method, path, w.owner, json=body)
            assert r.status_code in (409, 404), (path, r.text)
            if r.status_code == 409:
                assert r.json()["detail"]["code"] == "not_live", path


# ===========================================================================
# Size caps and linear-time text handling
# ===========================================================================


class TestSizeCaps:
    def test_proposal_html_css_and_nesting_caps(self, client, w):
        enquiry_id = detail_of(new_enquiry(client, w))
        call(client, "POST", f"/enquiries/{enquiry_id}/proposal", w.staff, json={})
        url = f"/enquiries/{enquiry_id}/proposal"
        too_long = call(client, "PUT", url, w.staff, json={"html": "x" * 500_001})
        assert too_long.status_code == 422
        assert "too long" in too_long.json()["detail"]["message"]
        assert (
            call(client, "PUT", url, w.staff, json={"css": "a{}" * 50_001}).status_code
            == 422
        )
        deep = call(client, "PUT", url, w.staff, json={"html": "<div>" * 300})
        assert deep.status_code == 422
        assert "nested" in deep.json()["detail"]["message"]
        # Nesting that real documents use, and lazily-closed lists, are fine.
        ok_html = "<div>" * 40 + "<ul>" + "<li>x" * 500 + "</ul>" + "</div>" * 40
        assert (
            call(client, "PUT", url, w.staff, json={"html": ok_html}).status_code == 200
        )
        check = call(
            client, "POST", f"{url}/check", w.staff, json={"html": "x" * 500_001}
        )
        assert check.status_code == 422

    def test_money_check_text_is_linear_and_reads_split_figures(self):
        from app.services.functions.proposals import (
            html_text,
            money_figures,
            nesting_depth,
        )

        assert money_figures(html_text("$5,<b>500</b> all in")) == [550_000]
        assert html_text("<p>Fish &amp; chips</p><script>$9,999</script>") == (
            "Fish & chips"
        )
        assert money_figures(html_text("<!-- $8,888 --> NZ$6,100.")) == [610_000]
        # Regexes like <(script|style)\b.*?</\1> and <[^>]+> take seconds to
        # minutes on these, and nh3 over 10 s on 30,000 nested <div>s; the
        # linear scan takes milliseconds. (Bounds have headroom for slow CI.)
        for payload in (
            "<script>" * 7_500,
            "<" * 200_000,
            "<!--" * 50_000,
            "<" + "a" * 200_000,
        ):
            started = time.perf_counter()
            money_figures(html_text(payload))
            nesting_depth(payload)
            assert time.perf_counter() - started < 1.0, payload[:20]
        nested = "<div>" * 30_000
        started = time.perf_counter()
        money_figures(html_text(nested))
        assert time.perf_counter() - started < 0.25
        started = time.perf_counter()
        assert nesting_depth(nested) == 30_000
        assert time.perf_counter() - started < 1.0

    def test_brand_template_caps(self, client, w):
        url = f"/venues/{w.venue.id}/brand"
        for body in (
            {"template_html": "x" * 500_001},
            {"template_css": "a{}" * 50_001},
            {"template_html": "<div>" * 300},
            {"footer_contact": "x" * 10_001},
            {"terms": ["Term"] * 101},
            {"tagline": "x" * 1_001},
        ):
            r = call(client, "PUT", url, w.staff, json=body)
            assert r.status_code == 422, (list(body), r.text)
        assert (
            call(
                client, "PUT", url, w.staff, json={"template_html": "<p>ok</p>"}
            ).status_code
            == 200
        )

    def test_reply_template_caps(self, client, w):
        base = {"venue_id": w.venue.id, "stage": "first_reply", "name": "Hello"}
        for body in (
            {**base, "body_html": "x" * 100_001},
            {**base, "subject": "x" * 1_001},
            {**base, "name": "x" * 1_001},
        ):
            r = call(client, "POST", "/reply-templates", w.staff, json=body)
            assert r.status_code == 422 and "too long" in r.json()["detail"]["message"]
        assert (
            call(
                client,
                "POST",
                "/reply-templates",
                w.staff,
                json={**base, "body_html": "x" * 100_000},
            ).status_code
            == 201
        )

    def test_venue_settings_and_area_caps(self, client, w):
        settings = f"/venues/{w.venue.id}/settings"
        assert (
            call(
                client, "PUT", settings, w.staff, json={"policies": "x" * 100_001}
            ).status_code
            == 422
        )
        links = [{"label": "L", "url": "https://x.example"}] * 101
        assert (
            call(
                client, "PUT", settings, w.staff, json={"menu_links": links}
            ).status_code
            == 422
        )
        areas = f"/venues/{w.venue.id}/areas"
        assert (
            call(
                client,
                "POST",
                areas,
                w.staff,
                json={"name": "A", "description": "x" * 100_001},
            ).status_code
            == 422
        )
        assert (
            call(client, "POST", areas, w.staff, json={"name": "x" * 1_001}).status_code
            == 422
        )
        r = call(
            client,
            "POST",
            areas,
            w.staff,
            json={"name": "Loft", "booking_types": ["seated_dining"] * 10_000},
        )
        assert r.status_code == 201 and r.json()["booking_types"] == ["seated_dining"]

    def test_preorder_template_caps(self, client, w):
        base = {"venue_id": w.venue.id, "name": "Menu"}
        for body in (
            {**base, "description": "x" * 100_001},
            {**base, "items": [{"name": "x" * 1_001}]},
            {**base, "items": [{"name": "Dish"}] * 1_001},
        ):
            r = call(client, "POST", "/preorder-templates", w.staff, json=body)
            assert r.status_code == 422, (list(body), r.text)


class TestRichTextSanitised:
    def test_reply_template_html_is_stored_clean(self, client, w):
        body = (
            '<p onclick="steal()">Hi {{client_name}},</p><script>alert(1)</script>'
            '<a href="javascript:alert(2)">bad</a> <a href="https://brew.bar">menu</a>'
            '<form action="https://evil.example"><input name="card"></form>'
            '<img src="https://evil.example/pixel.gif">'
        )
        r = call(
            client,
            "POST",
            "/reply-templates",
            w.staff,
            json={
                "venue_id": w.venue.id,
                "stage": "first_reply",
                "name": "Hi",
                "body_html": body,
            },
        )
        assert r.status_code == 201, r.text
        stored = r.json()["body_html"]
        for bad in (
            "<script",
            "onclick",
            "javascript:",
            "<form",
            "<input",
            "evil.example",
        ):
            assert bad not in stored, bad
        assert "{{client_name}}" in stored and 'href="https://brew.bar"' in stored
        patched = call(
            client,
            "PATCH",
            f"/reply-templates/{r.json()['id']}",
            w.staff,
            json={"body_html": '<p onmouseover="x()">Again</p>'},
        ).json()
        assert patched["body_html"] == "<p>Again</p>"
        deep = call(
            client,
            "PATCH",
            f"/reply-templates/{r.json()['id']}",
            w.staff,
            json={"body_html": "<div>" * 300},
        )
        assert deep.status_code == 422
        assert "reply template" in deep.json()["detail"]["message"]
