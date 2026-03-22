"""Response transform utility for connector tool results.

Transforms raw API responses into LLM-friendly format by selecting
and renaming fields based on a per-tool configuration.

Supports:
- Flat field mapping: {"id": "id", "stockVariantCode": "product_code"}
- Nested object access: {"supplier.name": "supplier_name"}
- Array sub-field mapping: {"lines[].stockCode": "stock_code"}
- Flattening arrays into parent: flatten=["lines"] produces one row per array item
"""


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


def _transform_item(
    item: dict,
    fields: dict[str, str],
    flatten: list[str] | None = None,
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
    for src, dest in regular.items():
        val = _resolve_dot_path(item, src)
        if val is not None:
            base[dest] = val

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
            for sub_src, sub_dest in sub_fields.items():
                val = _resolve_dot_path(sub_item, sub_src)
                if val is not None:
                    row[sub_dest] = val
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
) -> dict | list:
    """Transform a tool result payload using the response_transform config.

    Args:
        payload: Raw API response (dict or list).
        transform_config: {
            "enabled": bool,
            "fields": {"source.path": "output_name", "arr[].field": "name"},
            "flatten": ["arr_name"],
            "filters": [{"field": "...", "operator": "...", "value": "..."}]
        }

    Returns the transformed payload. Raw data is not modified in-place.
    """
    if not transform_config or not transform_config.get("enabled"):
        return payload

    fields = transform_config.get("fields")
    if not fields:
        return payload

    flatten = transform_config.get("flatten") or []
    filters = transform_config.get("filters") or []

    data, wrapper_key = _find_array(payload)

    if data is not None and len(data) > 0 and isinstance(data[0], dict):
        # Apply row-level filters before field mapping
        if filters:
            data = [item for item in data if _evaluate_filters(item, filters)]

        # Array of objects — transform each item
        transformed_items: list[dict] = []
        for item in data:
            result = _transform_item(item, fields, flatten)
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
            result = _transform_item(payload["data"], fields, flatten)
            return {**payload, "data": result}
        result = _transform_item(payload, fields, flatten)
        return result

    return payload
