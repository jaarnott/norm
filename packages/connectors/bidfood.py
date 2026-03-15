"""
Bidfood Connector Stub

TODO:
- Implement API authentication
- Product catalog sync
- Order submission
- Order status tracking
"""


class BidfoodConnector:
    """Stub connector for Bidfood supplier integration."""

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key

    def search_products(self, query: str) -> list[dict]:
        """Search Bidfood product catalog. Stub."""
        return []

    def submit_order(self, order: dict) -> dict:
        """Submit an order to Bidfood. Stub."""
        return {"status": "not_implemented"}
