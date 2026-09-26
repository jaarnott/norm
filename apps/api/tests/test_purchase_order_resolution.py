"""create_purchase_order's item resolution: the stock-code path.

The resolver scanned get_stock_items_raw, whose transform keeps {id, groupId,
groupName, name} — no suppliers[] — so a stock code could never match and a
code-only request always failed as "not found". Codes now come from the
engine-only get_stock_items_with_codes list; without codes the name paths
decide instead of failing.
"""

from app.agents.internal_tools import _resolve_stock_items

SLIM = [
    {"id": "i1", "name": "ASAHI SUPER DRY", "groupName": "Bottled"},
    {"id": "i2", "name": "CORONA 24PK", "groupName": "Bottled"},
]
WITH_CODES = [
    {
        "id": "i1",
        "name": "ASAHI SUPER DRY",
        "suppliers": [{"supplierId": "s1", "stockCode": "DPASAHI12AUN"}],
    },
    {
        "id": "i2",
        "name": "CORONA 24PK",
        "suppliers": [{"supplierId": "s2", "stockCode": "985326"}],
    },
]


def test_a_code_matches_when_the_list_carries_suppliers():
    resolved, ambiguous, failed = _resolve_stock_items(
        [{"name": "anything", "stock_code": "dpasahi12aun", "quantity": 2}], WITH_CODES
    )
    assert resolved == [
        {"itemId": "i1", "quantity": 2, "matched_name": "ASAHI SUPER DRY"}
    ]
    assert not ambiguous and not failed


def test_an_unknown_code_fails_only_when_codes_were_checkable():
    resolved, _, failed = _resolve_stock_items(
        [{"name": "Nope", "stock_code": "ZZZ", "quantity": 1}], WITH_CODES
    )
    assert not resolved and failed[0]["reason"] == "Stock code 'ZZZ' not found"


def test_the_slim_list_falls_through_to_the_name_instead_of_failing():
    # The old behaviour: "Stock code 'DPASAHI12AUN' not found" — every time.
    resolved, _, failed = _resolve_stock_items(
        [{"name": "Asahi Super Dry", "stock_code": "DPASAHI12AUN", "quantity": 3}], SLIM
    )
    assert resolved == [
        {"itemId": "i1", "quantity": 3, "matched_name": "ASAHI SUPER DRY"}
    ]
    assert not failed


def test_an_item_id_still_wins_over_everything():
    resolved, _, _ = _resolve_stock_items(
        [{"name": "x", "itemId": "i2", "stock_code": "wrong", "quantity": 1}],
        WITH_CODES,
    )
    assert resolved[0]["itemId"] == "i2"
