"""Mock tool implementations for report execution.

These return realistic mock data. In production these would query
actual data stores.
"""

import random
from datetime import datetime, timedelta


def query_sales_data(
    time_range: dict | None = None,
    venue_name: str | None = None,
    product_name: str | None = None,
) -> list[dict]:
    """Return mock sales records."""
    base_date = datetime(2024, 1, 1)
    records = []
    venues = [venue_name] if venue_name else ["La Zeppa"]
    products = [product_name] if product_name else ["Jim Beam", "Corona", "Sauvignon Blanc"]

    for day_offset in range(30):
        date = base_date + timedelta(days=day_offset)
        for v in venues:
            for p in products:
                qty = random.randint(2, 20)
                price = round(random.uniform(8.0, 45.0), 2)
                records.append({
                    "date": date.strftime("%Y-%m-%d"),
                    "venue": v,
                    "product": p,
                    "quantity": qty,
                    "revenue": round(qty * price, 2),
                    "cost": round(qty * price * 0.6, 2),
                })
    return records


def query_inventory(
    venue_name: str | None = None,
    product_name: str | None = None,
) -> list[dict]:
    """Return mock inventory snapshot."""
    venues = [venue_name] if venue_name else ["La Zeppa"]
    products = [product_name] if product_name else ["Jim Beam", "Corona", "Sauvignon Blanc"]

    records = []
    for v in venues:
        for p in products:
            records.append({
                "venue": v,
                "product": p,
                "stock_level": random.randint(0, 50),
                "unit": "cases",
                "reorder_point": 10,
                "last_ordered": "2024-01-15",
            })
    return records


def aggregate_by_period(
    data: list[dict],
    group_by: str = "day",
    metrics: list[str] | None = None,
) -> list[dict]:
    """Aggregate data by the specified period."""
    metrics = metrics or ["revenue"]
    groups: dict[str, dict] = {}

    for record in data:
        if group_by == "venue":
            key = record.get("venue", "unknown")
        elif group_by == "product":
            key = record.get("product", "unknown")
        elif group_by == "week":
            date = datetime.strptime(record.get("date", "2024-01-01"), "%Y-%m-%d")
            key = f"W{date.isocalendar()[1]}"
        elif group_by == "month":
            key = record.get("date", "2024-01-01")[:7]
        else:  # day
            key = record.get("date", "unknown")

        if key not in groups:
            groups[key] = {"period": key, **{m: 0 for m in metrics}}

        for m in metrics:
            groups[key][m] = round(groups[key].get(m, 0) + record.get(m, 0), 2)

    return sorted(groups.values(), key=lambda x: x["period"])


def format_report(
    aggregated: list[dict],
    report_type: str = "summary",
    metrics: list[str] | None = None,
    group_by: str = "day",
) -> dict:
    """Format aggregated data into a report structure."""
    metrics = metrics or ["revenue"]

    totals = {}
    for m in metrics:
        totals[m] = round(sum(row.get(m, 0) for row in aggregated), 2)

    return {
        "report_type": report_type,
        "group_by": group_by,
        "period_count": len(aggregated),
        "totals": totals,
        "rows": aggregated[:20],  # cap at 20 rows for display
        "generated_at": datetime.now().isoformat(),
    }
