"""What a role reaches (staff access foundation, docs/plans/staff-access.md
section 11.3): the Access value, the Loaded role mapping, the venue roles, and
the roles routes that carry the new fields."""

import importlib.util
import pathlib
import uuid

from app.auth.permissions import STANDARD_ROLES, VENUE_ROLES
from app.db.models import Role
from app.services import role_access
from app.services.role_access import Access
from tests.conftest import _make_membership, _make_user
from tests.org_helpers import auth_headers, make_org, seed_system_roles


def _custom(db, org, name, **fields):
    role = Role(
        id=str(uuid.uuid4()),
        organization_id=org.id,
        name=name,
        display_name=fields.pop("display_name", name.title()),
        is_system=False,
        permissions=fields.pop("permissions", ["tasks:read"]),
        **fields,
    )
    db.add(role)
    db.flush()
    return role


def _owner_world(db):
    roles = seed_system_roles(db)
    org = make_org(db, "Lott Group")
    owner = _make_user(db)
    _make_membership(db, owner, org).role_id = roles["owner"].id
    db.flush()
    return roles, org, owner


# ---------------------------------------------------------------------------
# Access
# ---------------------------------------------------------------------------


class TestAccess:
    def test_null_is_everything_and_an_empty_agents_list_is_staff(self):
        everything = Access(None, None)
        staff = Access(frozenset(), frozenset({"training"}))
        narrow = Access(frozenset({"hr"}), frozenset({"training"}))
        assert not everything.staff and staff.staff and not narrow.staff
        assert role_access.allows_agent(everything, "procurement")
        assert not role_access.allows_agent(staff, "procurement")
        assert role_access.allows_agent(narrow, "hr")
        assert not role_access.allows_agent(narrow, "procurement")
        assert role_access.allows_app(everything, "anything")
        assert role_access.allows_app(staff, "training")
        assert not role_access.allows_app(staff, "kitchen")
        # Norm Core's tools: everyone but venue staff
        assert role_access.core_allowed(everything)
        assert role_access.core_allowed(narrow)
        assert not role_access.core_allowed(staff)

    def test_an_app_of_an_allowed_agent_is_allowed(self):
        narrow = Access(frozenset({"hr"}), frozenset({"training"}))
        assert role_access.allows_app(narrow, "loaded-kitchen", member="hr")
        assert not role_access.allows_app(
            narrow, "loaded-kitchen", member="procurement"
        )
        assert not role_access.allows_app(narrow, "loaded-kitchen")
        # a role with no agent gets nothing through the member door
        staff = Access(frozenset(), frozenset())
        assert not role_access.allows_app(staff, "loaded-kitchen", member="hr")

    def test_for_role_reads_nulls_lists_and_empties(self, db_session):
        roles = seed_system_roles(db_session)
        org = make_org(db_session, "O")
        assert role_access.for_role(None) == Access(None, None)
        assert role_access.for_role(roles["owner"]) == Access(None, None)
        assert role_access.for_role(roles["team_member"]) == Access(
            frozenset(), frozenset()
        )
        head = _custom(
            db_session, org, "head_chef", agents=[], apps=["kitchen", "managers"]
        )
        assert role_access.for_role(head) == Access(
            frozenset(), frozenset({"kitchen", "managers"})
        )
        assert role_access.for_role(head).staff

    def test_for_user_platform_admin_and_no_membership_get_everything(self, db_session):
        roles = seed_system_roles(db_session)
        org = make_org(db_session, "O")
        admin = _make_user(db_session, role="admin")
        _make_membership(db_session, admin, org).role_id = roles["team_member"].id
        nobody = _make_user(db_session)
        db_session.flush()
        assert role_access.for_user(db_session, admin.id) == Access(None, None)
        assert role_access.for_user(db_session, nobody.id) == Access(None, None)
        assert role_access.for_user(db_session, None) == Access(None, None)

    def test_for_user_follows_the_membership_of_the_organisation_asked_for(
        self, db_session
    ):
        roles = seed_system_roles(db_session)
        a, b = make_org(db_session, "A"), make_org(db_session, "B")
        user = _make_user(db_session)
        _make_membership(db_session, user, a).role_id = roles["owner"].id
        _make_membership(db_session, user, b).role_id = roles["team_member"].id
        db_session.flush()
        assert role_access.for_user(db_session, user.id) == Access(None, None)
        assert role_access.for_user(db_session, user.id, b.id) == Access(
            frozenset(), frozenset()
        )
        # a membership with no role limits nothing
        loose = _make_user(db_session)
        _make_membership(db_session, loose, a)
        db_session.flush()
        assert role_access.for_user(db_session, loose.id) == Access(None, None)


# ---------------------------------------------------------------------------
# Loaded role mapping and the venue roles
# ---------------------------------------------------------------------------


class TestLoadedRoles:
    def test_matches_case_insensitively_custom_before_system(self, db_session):
        roles = seed_system_roles(db_session)
        org = make_org(db_session, "O")
        other = make_org(db_session, "Other")
        kitchen = _custom(db_session, org, "kitchen", loaded_roles=["Chef", "Cook"])
        _custom(db_session, other, "floor", loaded_roles=["Bartender"])
        roles["team_member"].loaded_roles = ["chef", "Bartender"]
        db_session.flush()

        assert (
            role_access.role_for_loaded_roles(db_session, org.id, ["CHEF"]) is kitchen
        )
        assert (
            role_access.role_for_loaded_roles(db_session, org.id, [" cook "]) is kitchen
        )
        # the system role catches what no custom role of THIS org maps
        assert (
            role_access.role_for_loaded_roles(db_session, org.id, ["Bartender"])
            is roles["team_member"]
        )
        assert role_access.role_for_loaded_roles(db_session, org.id, ["Owner"]) is None
        assert role_access.role_for_loaded_roles(db_session, org.id, []) is None
        assert role_access.role_for_loaded_roles(db_session, org.id, [""]) is None

    def test_conflict_names_the_role_that_already_has_it(self, db_session):
        seed_system_roles(db_session)
        org = make_org(db_session, "O")
        kitchen = _custom(
            db_session, org, "kitchen", display_name="Kitchen", loaded_roles=["Chef"]
        )
        assert (
            role_access.loaded_role_conflict(db_session, org.id, ["Barista", "chef"])
            == "'chef' already maps to Kitchen"
        )
        assert role_access.loaded_role_conflict(db_session, org.id, ["Barista"]) is None
        # editing the role that holds it is not a conflict with itself
        assert (
            role_access.loaded_role_conflict(
                db_session, org.id, ["Chef"], exclude_role_id=kitchen.id
            )
            is None
        )
        # another organisation's mapping is not this one's
        other = make_org(db_session, "Other")
        assert role_access.loaded_role_conflict(db_session, other.id, ["Chef"]) is None

    def test_ensure_venue_roles_creates_four_custom_roles_once(self, db_session):
        seed_system_roles(db_session)
        org = make_org(db_session, "O")
        made = role_access.ensure_venue_roles(db_session, org.id)
        assert [r.name for r in made] == list(VENUE_ROLES)
        by_name = {r.name: r for r in made}
        for name, defn in VENUE_ROLES.items():
            r = by_name[name]
            assert r.organization_id == org.id and r.is_system is False
            assert r.display_name == defn["display_name"]
            assert r.permissions == defn["permissions"]
            assert r.agents == defn["agents"] and r.apps == defn["apps"]
            assert r.loaded_roles == defn["loaded_roles"]
        assert by_name["kitchen"].apps == ["kitchen"]
        assert by_name["venue_manager"].agents is None
        assert by_name["venue_manager"].apps is None
        assert (
            by_name["venue_manager"].permissions
            == STANDARD_ROLES["manager"]["permissions"]
        )
        assert (
            by_name["head_chef"].permissions
            == STANDARD_ROLES["team_member"]["permissions"]
        )

        # the owner's edits survive a second call, and nothing is duplicated
        by_name["kitchen"].display_name = "Back of house"
        by_name["kitchen"].apps = ["kitchen", "training"]
        db_session.flush()
        again = role_access.ensure_venue_roles(db_session, org.id)
        assert {r.id for r in again} == {r.id for r in made}
        assert (
            db_session.query(Role).filter(Role.organization_id == org.id).count() == 4
        )
        kitchen = next(r for r in again if r.name == "kitchen")
        assert kitchen.display_name == "Back of house" and kitchen.apps == [
            "kitchen",
            "training",
        ]
        # and the venue roles' mappings are read back through the mapper
        assert (
            role_access.role_for_loaded_roles(db_session, org.id, ["head chef"]).name
            == "head_chef"
        )

    def test_ensure_venue_roles_leaves_the_owners_mappings_alone(self, db_session):
        """An owner's own role already maps 'Chef' and 'Barista': the shipped
        guesses skip them, or the new Kitchen role could never be saved in the
        editor ("'Chef' already maps to Cooks")."""
        seed_system_roles(db_session)
        org = make_org(db_session, "O")
        cooks = Role(
            id=str(uuid.uuid4()),
            organization_id=org.id,
            name="cooks",
            display_name="Cooks",
            is_system=False,
            permissions=[],
            loaded_roles=["chef", "Barista"],
        )
        db_session.add(cooks)
        db_session.flush()
        by_name = {
            r.name: r for r in role_access.ensure_venue_roles(db_session, org.id)
        }
        assert "Chef" not in by_name["kitchen"].loaded_roles
        assert "Cook" in by_name["kitchen"].loaded_roles
        assert "Barista" not in by_name["front_of_house"].loaded_roles
        for role in by_name.values():
            assert (
                role_access.loaded_role_conflict(
                    db_session, org.id, role.loaded_roles, exclude_role_id=role.id
                )
                is None
            )
        assert role_access.role_for_loaded_roles(db_session, org.id, ["CHEF"]) is cooks

    def test_the_venue_roles_do_not_map_one_loaded_role_twice(self):
        seen: dict[str, str] = {}
        for name, defn in VENUE_ROLES.items():
            for loaded in defn["loaded_roles"]:
                assert loaded.casefold() not in seen, (loaded, name, seen)
                seen[loaded.casefold()] = name


# ---------------------------------------------------------------------------
# The migration mirrors the code
# ---------------------------------------------------------------------------


def test_the_migration_writes_what_the_code_defines():
    """The system team_member row is rewritten once by bi4k6m8o0q2t; the code
    and the migration must agree, or a fresh database and a migrated one hold
    different roles."""
    path = (
        pathlib.Path(__file__).parents[1]
        / "alembic/versions/bi4k6m8o0q2t_staff_access.py"
    )
    spec = importlib.util.spec_from_file_location("mig", path)
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)
    team_member = STANDARD_ROLES["team_member"]
    assert mig.TEAM_MEMBER_PERMISSIONS == team_member["permissions"]
    assert team_member["agents"] == [] and team_member["apps"] == []
    assert not set(mig.TEAM_MEMBER_DROPPED) & set(team_member["permissions"])
    for name in ("owner", "manager", "payroll_admin"):
        assert STANDARD_ROLES[name]["agents"] is None
        assert STANDARD_ROLES[name]["apps"] is None


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


class TestRolesRoutes:
    def test_list_shows_the_system_team_member_with_no_agents_and_no_apps(
        self, client, db_session
    ):
        _, org, owner = _owner_world(db_session)
        r = client.get(
            f"/api/organizations/{org.id}/roles", headers=auth_headers(owner)
        )
        assert r.status_code == 200, r.text
        rows = {x["name"]: x for x in r.json()["roles"]}
        assert rows["team_member"]["agents"] == []
        assert rows["team_member"]["apps"] == []
        assert rows["team_member"]["loaded_roles"] == []
        assert rows["owner"]["agents"] is None and rows["owner"]["apps"] is None

    def test_create_and_update_carry_agents_apps_and_loaded_roles(
        self, client, db_session
    ):
        _, org, owner = _owner_world(db_session)
        url = f"/api/organizations/{org.id}/roles"
        r = client.post(
            url,
            json={
                "name": "kitchen",
                "display_name": "Kitchen",
                "permissions": ["tasks:read"],
                "agents": [],
                "apps": ["kitchen", " training ", "kitchen"],
                "loaded_roles": [" Chef ", "cook", "CHEF", ""],
            },
            headers=auth_headers(owner),
        )
        assert r.status_code == 200, r.text
        made = r.json()
        assert made["agents"] == []
        assert made["apps"] == ["kitchen", "training"]
        assert made["loaded_roles"] == ["Chef", "cook"]
        assert role_access.for_role(db_session.get(Role, made["id"])).staff

        # a body without agents/apps leaves them; an explicit null lifts the limit
        r = client.put(
            f"{url}/{made['id']}",
            json={"loaded_roles": ["Chef", "Sous Chef"]},
            headers=auth_headers(owner),
        )
        assert r.status_code == 200, r.text
        assert r.json()["agents"] == [] and r.json()["apps"] == ["kitchen", "training"]
        assert r.json()["loaded_roles"] == ["Chef", "Sous Chef"]
        r = client.put(
            f"{url}/{made['id']}",
            json={"agents": None, "apps": ["kitchen"]},
            headers=auth_headers(owner),
        )
        assert r.status_code == 200, r.text
        assert r.json()["agents"] is None and r.json()["apps"] == ["kitchen"]
        # a role with no limit on agents is not staff
        db_session.expire_all()
        assert not role_access.for_role(db_session.get(Role, made["id"])).staff

    def test_a_loaded_role_mapped_elsewhere_is_a_400(self, client, db_session):
        _, org, owner = _owner_world(db_session)
        url = f"/api/organizations/{org.id}/roles"
        kitchen = _custom(
            db_session, org, "kitchen", display_name="Kitchen", loaded_roles=["Chef"]
        )
        floor = _custom(db_session, org, "floor", loaded_roles=["Bartender"])
        r = client.post(
            url,
            json={
                "name": "pastry",
                "display_name": "Pastry",
                "loaded_roles": ["Pastry Chef", "chef"],
            },
            headers=auth_headers(owner),
        )
        assert r.status_code == 400
        assert r.json()["detail"] == "'chef' already maps to Kitchen"
        r = client.put(
            f"{url}/{floor.id}",
            json={"loaded_roles": ["Bartender", "Chef"]},
            headers=auth_headers(owner),
        )
        assert r.status_code == 400
        assert r.json()["detail"] == "'Chef' already maps to Kitchen"
        # the role that holds the name can keep it
        r = client.put(
            f"{url}/{kitchen.id}",
            json={"loaded_roles": ["Chef", "Cook"]},
            headers=auth_headers(owner),
        )
        assert r.status_code == 200, r.text
        assert r.json()["loaded_roles"] == ["Chef", "Cook"]

    def test_system_roles_still_cannot_be_edited(self, client, db_session):
        roles, org, owner = _owner_world(db_session)
        r = client.put(
            f"/api/organizations/{org.id}/roles/{roles['team_member'].id}",
            json={"apps": ["kitchen"], "loaded_roles": ["Chef"]},
            headers=auth_headers(owner),
        )
        assert r.status_code == 400
        db_session.refresh(roles["team_member"])
        assert (
            roles["team_member"].apps == [] and roles["team_member"].loaded_roles == []
        )
