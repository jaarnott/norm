"""RETIRED 1 Oct 2026 — do not run.

This installed the `loadedhub.update_stock_item` consolidator (and, earlier,
the stock-item read surface). update_stock_item was superseded by
manage_stock_item (op 'update') on 26 Sep 2026 (f42e4c1) and deleted on
1 Oct 2026 (scripts/sync_delete_update_stock_item.py). Re-running the old
script would have reinstalled it, so its body is gone; see git history for
the original. The endpoints it set up (get_stock_item_full,
update_stock_item_raw, get_stock_items_raw) remain and are used by
get_stock and manage_stock_item.
"""

if __name__ == "__main__":
    raise SystemExit(
        "sync_stock_item_consolidators.py is retired: update_stock_item was "
        "deleted on 1 Oct 2026 — stock writes are manage_stock_item "
        "(scripts/sync_manage_stock_item_config.py)."
    )
