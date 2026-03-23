"""Report execution planner."""


def create_report_plan(interpretation: dict) -> list[dict]:
    """Given an interpretation, produce a list of execution steps.

    Each step is {"step": str, "action": str, "params": dict}.
    """
    extracted = interpretation.get("extracted_fields", {})
    data_sources = extracted.get("data_sources", ["sales"])
    metrics = extracted.get("metrics", ["revenue"])
    time_range = extracted.get("time_range", {})
    group_by = extracted.get("group_by", "day")
    venue_name = extracted.get("venue_name")
    product_name = extracted.get("product_name")

    steps: list[dict] = []

    # Step 1: resolve entities if venue/product specified
    if venue_name or product_name:
        steps.append(
            {
                "step": "resolve_entities",
                "action": "resolve",
                "params": {
                    "venue_name": venue_name,
                    "product_name": product_name,
                },
            }
        )

    # Step 2: fetch data for each source
    for source in data_sources:
        steps.append(
            {
                "step": f"fetch_{source}",
                "action": f"query_{source}_data",
                "params": {
                    "time_range": time_range,
                    "venue_name": venue_name,
                    "product_name": product_name,
                },
            }
        )

    # Step 3: aggregate
    steps.append(
        {
            "step": "aggregate",
            "action": "aggregate_by_period",
            "params": {
                "group_by": group_by,
                "metrics": metrics,
            },
        }
    )

    # Step 4: format
    steps.append(
        {
            "step": "format",
            "action": "format_report",
            "params": {
                "report_type": extracted.get("report_type", "summary"),
                "metrics": metrics,
                "group_by": group_by,
            },
        }
    )

    return steps
