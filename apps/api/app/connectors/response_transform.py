"""Response transform utility for connector tool results.

Transforms raw API responses into LLM-friendly format by selecting
and renaming fields based on a per-tool configuration.

Supports:
- Flat field mapping: {"id": "id", "stockVariantCode": "product_code"}
- Nested object access: {"supplier.name": "supplier_name"}
- Array sub-field mapping: {"lines[].stockCode": "stock_code"}
- Flattening arrays into parent: flatten=["lines"] produces one row per array item
- Timezone normalization: convert all ISO datetime strings to venue timezone
"""

import re
from datetime import datetime
from zoneinfo import ZoneInfo

# Matches ISO 8601 datetime strings with timezone offset (e.g., 2026-03-26T22:10:00+00:00)
_ISO_DATETIME_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[+-]\d{2}:\d{2}|Z)$"
)


def normalize_timezones(obj, target_tz: ZoneInfo):
    """Recursively find ISO datetime strings and convert to target timezone."""
    if isinstance(obj, dict):
        return {k: normalize_timezones(v, target_tz) for k, v in obj.items()}
    if isinstance(obj, list):
        return [normalize_timezones(item, target_tz) for item in obj]
    if isinstance(obj, str) and _ISO_DATETIME_RE.match(obj):
        try:
            dt = datetime.fromisoformat(obj.replace("Z", "+00:00"))
            return dt.astimezone(target_tz).isoformat()
        except (ValueError, TypeError):
            return obj
    return obj


def _resolve_dot_path(obj: dict, path: str):
    """Traverse a nested dict using a dot-separated path.

    Returns None if any segment is missing.
    Does NOT traverse into arrays — use _transform_item for that.
    """
    current = obj
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def _find_array(payload: dict | list) -> tuple[list | None, str | None]:
    """Find the primary data array in a payload.

    Returns (array, key) where key is the wrapper key (e.g., "data")
    or None if the payload is itself a list.
    """
    if isinstance(payload, list):
        return payload, None
    if isinstance(payload, dict):
        for key in ("data", "items", "lines", "results"):
            val = payload.get(key)
            if isinstance(val, list):
                return val, key
            if key == "data" and isinstance(val, dict):
                for inner_key in ("items", "lines", "results", "data"):
                    inner = val.get(inner_key)
                    if isinstance(inner, list):
                        return inner, f"data.{inner_key}"
    return None, None


def _parse_field_dest(dest_str: str) -> tuple[str, dict[str, str]]:
    """Parse ``'output_name|round:2|tz|dow'`` into ``('output_name', {'round': '2', 'tz': '', 'dow': ''})``."""
    parts = dest_str.split("|")
    name = parts[0]
    options: dict[str, str] = {}
    for part in parts[1:]:
        if ":" in part:
            k, v = part.split(":", 1)
            options[k.strip()] = v.strip()
        elif part.strip():
            options[part.strip()] = ""
    return name, options


def _apply_field_options(
    value, options: dict[str, str], venue_tz: "ZoneInfo | None" = None
):
    """Apply per-field options (rounding, timezone normalization) to a resolved value."""
    if "round" in options and isinstance(value, (int, float)):
        dp = int(options["round"])
        value = round(value, dp) if dp > 0 else int(round(value, 0))
    if (
        "tz" in options
        and isinstance(value, str)
        and venue_tz
        and _ISO_DATETIME_RE.match(value)
    ):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            value = dt.astimezone(venue_tz).isoformat()
        except (ValueError, TypeError):
            pass
    return value


def _get_day_of_week(value) -> str | None:
    """Extract day-of-week from an ISO datetime string."""
    if isinstance(value, str) and _ISO_DATETIME_RE.match(value):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt.strftime("%A")  # "Tuesday"
        except (ValueError, TypeError):
            pass
    return None


def _transform_item(
    item: dict,
    fields: dict[str, str],
    flatten: list[str] | None = None,
    venue_tz: "ZoneInfo | None" = None,
) -> list[dict] | dict:
    """Transform a single item using the field mapping.

    Fields with an empty output_name are excluded (toggled off by the user).
    Fields containing '[]' are array sub-field mappings.
    If an array name is in the flatten list, returns a list of dicts
    (one per array element with parent fields merged in).
    """
    flatten = flatten or []

    # Separate regular fields from array sub-fields
    regular: dict[str, str] = {}
    array_fields: dict[str, dict[str, str]] = {}  # {arr_name: {sub_path: output_name}}
    for src, dest in fields.items():
        if not dest:
            continue  # excluded
        if "[]." in src:
            arr_name, sub_path = src.split("[].", 1)
            array_fields.setdefault(arr_name, {})[sub_path] = dest
        else:
            regular[src] = dest

    # Build base object from regular fields
    base: dict = {}
    for src, dest_raw in regular.items():
        dest, opts = _parse_field_dest(dest_raw)
        val = _resolve_dot_path(item, src)
        if val is not None:
            base[dest] = _apply_field_options(val, opts, venue_tz=venue_tz)
            # Add day-of-week sibling field if requested
            if "dow" in opts:
                dow = _get_day_of_week(base[dest])
                if dow:
                    base[f"{dest}_dayOfWeek"] = dow

    # Process array fields
    for arr_name, sub_fields in array_fields.items():
        arr_data = item.get(arr_name)
        if not isinstance(arr_data, list):
            continue

        transformed_arr = []
        for sub_item in arr_data:
            if not isinstance(sub_item, dict):
                continue
            row: dict = {}
            for sub_src, sub_dest_raw in sub_fields.items():
                sub_dest, sub_opts = _parse_field_dest(sub_dest_raw)
                val = _resolve_dot_path(sub_item, sub_src)
                if val is not None:
                    row[sub_dest] = _apply_field_options(
                        val, sub_opts, venue_tz=venue_tz
                    )
                    if "dow" in sub_opts:
                        dow = _get_day_of_week(row[sub_dest])
                        if dow:
                            row[f"{sub_dest}_dayOfWeek"] = dow
            if row:
                transformed_arr.append(row)

        if arr_name in flatten:
            # Flatten: return one row per array element with base fields merged
            return [{**base, **row} for row in transformed_arr]
        else:
            # Keep nested
            base[arr_name] = transformed_arr

    return base


def _evaluate_filters(item: dict, filters: list[dict]) -> bool:
    """Evaluate filter conditions against a single item. All must pass (AND logic)."""
    for f in filters:
        field_val = _resolve_dot_path(item, f.get("field", ""))
        op = f.get("operator", "")
        target = f.get("value", "")
        if op == "is_empty":
            if field_val is not None and field_val != "" and field_val != []:
                return False
        elif op == "is_not_empty":
            if field_val is None or field_val == "" or field_val == []:
                return False
        elif op == "equals":
            if str(field_val or "").lower() != str(target).lower():
                return False
        elif op == "not_equals":
            if str(field_val or "").lower() == str(target).lower():
                return False
        elif op == "contains":
            if target.lower() not in str(field_val or "").lower():
                return False
        elif op == "gt":
            try:
                if float(field_val or 0) <= float(target):
                    return False
            except (ValueError, TypeError):
                return False
        elif op == "lt":
            try:
                if float(field_val or 0) >= float(target):
                    return False
            except (ValueError, TypeError):
                return False
    return True


def apply_response_transform(
    payload: dict | list,
    transform_config: dict,
    venue_timezone: str | None = None,
) -> dict | list:
    """Transform a tool result payload using the response_transform config.

    Args:
        payload: Raw API response (dict or list).
        transform_config: {
            "enabled": bool,
            "fields": {"source.path": "output_name|tz|dow", "arr[].field": "name"},
            "flatten": ["arr_name"],
            "filters": [{"field": "...", "operator": "...", "value": "..."}]
        }
        venue_timezone: IANA timezone name for tz/dow field options (e.g., "Pacific/Auckland").

    Returns the transformed payload. Raw data is not modified in-place.
    """
    if not transform_config or not transform_config.get("enabled"):
        return payload

    fields = transform_config.get("fields")
    if not fields:
        return payload

    flatten = transform_config.get("flatten") or []
    filters = transform_config.get("filters") or []

    # Resolve venue timezone for tz/dow field options
    venue_tz = None
    if venue_timezone:
        try:
            venue_tz = ZoneInfo(venue_timezone)
        except Exception:
            pass

    data, wrapper_key = _find_array(payload)

    if data is not None and len(data) > 0 and isinstance(data[0], dict):
        # Apply row-level filters before field mapping
        if filters:
            data = [item for item in data if _evaluate_filters(item, filters)]

        # Array of objects — transform each item
        transformed_items: list[dict] = []
        for item in data:
            result = _transform_item(item, fields, flatten, venue_tz=venue_tz)
            if isinstance(result, list):
                transformed_items.extend(result)  # flattened
            else:
                transformed_items.append(result)

        if wrapper_key is None:
            return transformed_items

        # Reconstruct the wrapper structure
        if "." in wrapper_key:
            outer_key, inner_key = wrapper_key.split(".", 1)
            out = {**payload}
            out[outer_key] = {**payload[outer_key], inner_key: transformed_items}
            return out

        out = {**payload}
        out[wrapper_key] = transformed_items
        return out

    # Single object or non-standard structure
    if isinstance(payload, dict):
        if "data" in payload and isinstance(payload["data"], dict):
            result = _transform_item(
                payload["data"], fields, flatten, venue_tz=venue_tz
            )
            return {**payload, "data": result}
        result = _transform_item(payload, fields, flatten, venue_tz=venue_tz)
        return result

    return payload
