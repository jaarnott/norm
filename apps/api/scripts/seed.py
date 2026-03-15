"""Seed the database with sample data."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.db.engine import SessionLocal
from app.db.models import Venue, Supplier, Product, ProductAlias, User, AgentConfig, AgentConnectorBinding, ConnectorSpec
from app.auth.security import hash_password
from app.data.seed import CONNECTOR_SPECS


def seed():
    db = SessionLocal()

    # Core data (venues, products, etc.)
    if not db.query(Venue).first():
        # Default admin user
        if not db.query(User).filter(User.email == "admin@norm.local").first():
            admin = User(
                id="admin-seed",
                email="admin@norm.local",
                hashed_password=hash_password("changeme123"),
                full_name="Admin",
                role="admin",
            )
            db.add(admin)

        # Venues
        v1 = Venue(id="v1", name="La Zeppa", location="Auckland CBD")
        v2 = Venue(id="v2", name="Mr Murdoch's", location="Auckland CBD")
        v3 = Venue(id="v3", name="Freeman & Grey", location="Auckland CBD")
        db.add_all([v1, v2, v3])

        # Suppliers
        s1 = Supplier(id="s1", name="Bidfood")
        s2 = Supplier(id="s2", name="Generic Supplier")
        db.add_all([s1, s2])

        # Products
        p1 = Product(
            id="p1",
            name="Jim Beam White Label Bourbon 700ml x 12",
            supplier_id="s1",
            category="spirits",
            unit="case",
            pack_size="700ml x 12",
        )
        p2 = Product(
            id="p2",
            name="Corona Extra 330ml x 24",
            supplier_id="s1",
            category="beer",
            unit="case",
            pack_size="330ml x 24",
        )
        db.add_all([p1, p2])

        # Aliases
        aliases = [
            ProductAlias(id="a1", product_id="p1", alias="jim beam"),
            ProductAlias(id="a2", product_id="p1", alias="jb"),
            ProductAlias(id="a3", product_id="p1", alias="jim beam white"),
            ProductAlias(id="a4", product_id="p1", alias="jim beam white label"),
            ProductAlias(id="a5", product_id="p2", alias="corona"),
            ProductAlias(id="a6", product_id="p2", alias="corona extra"),
            ProductAlias(id="a7", product_id="p2", alias="coronas"),
        ]
        db.add_all(aliases)
        print("Seeded core data: 3 venues, 2 suppliers, 2 products, 7 aliases, 1 admin user.")

    # Agent configs (idempotent — separate from core data)
    if not db.query(AgentConfig).first():
        agent_configs = [
            AgentConfig(agent_slug="procurement", display_name="Procurement Agent",
                        description="Orders stock from suppliers for venues"),
            AgentConfig(agent_slug="hr", display_name="HR Agent",
                        description="Sets up new employees at venues"),
            AgentConfig(agent_slug="reports", display_name="Reports Agent",
                        description="Generates sales and inventory reports"),
            AgentConfig(agent_slug="router", display_name="Router",
                        description="Classifies messages and routes to the right agent"),
        ]
        db.add_all(agent_configs)

        agent_bindings = [
            AgentConnectorBinding(agent_slug="procurement", connector_name="bidfood",
                capabilities=[
                    {"action": "create_order", "label": "Submit purchase orders", "enabled": True},
                    {"action": "check_stock", "label": "Check stock availability", "enabled": True},
                ]),
            AgentConnectorBinding(agent_slug="hr", connector_name="bamboohr",
                capabilities=[
                    {"action": "create_employee", "label": "Create new employees", "enabled": True},
                    {"action": "terminate_employee", "label": "Terminate employees", "enabled": True},
                ]),
            AgentConnectorBinding(agent_slug="hr", connector_name="deputy",
                capabilities=[
                    {"action": "create_roster", "label": "Create shift/roster", "enabled": True},
                    {"action": "list_rosters", "label": "View upcoming rosters", "enabled": True},
                ]),
            AgentConnectorBinding(agent_slug="hr", connector_name="loadedhub",
                capabilities=[
                    {"action": "get_roster", "label": "View rosters by date", "enabled": True},
                    {"action": "get_shifts", "label": "View shifts in a roster", "enabled": True},
                    {"action": "create_shift", "label": "Create rostered shifts", "enabled": True},
                    {"action": "update_shift", "label": "Update rostered shifts", "enabled": True},
                    {"action": "delete_shift", "label": "Delete rostered shifts", "enabled": True},
                ]),
        ]
        db.add_all(agent_bindings)
        print("Seeded agent configs: 4 agents, 2 connector bindings.")
    else:
        print("Agent configs already seeded. Skipping.")

    # Connector specs (idempotent)
    if not db.query(ConnectorSpec).first():
        for spec_data in CONNECTOR_SPECS:
            spec = ConnectorSpec(**spec_data)
            db.add(spec)
        print(f"Seeded connector specs: {len(CONNECTOR_SPECS)} specs.")
    else:
        print("Connector specs already seeded. Skipping.")

    db.commit()
    db.close()


if __name__ == "__main__":
    seed()
