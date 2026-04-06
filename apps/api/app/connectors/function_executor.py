"""Execute Python functions for consolidator tools.

Functions run in a restricted environment with access to connector APIs
via the `call_api` helper. No file I/O, no imports, no network access
except through `call_api`.
"""

import datetime
import json
import logging
import math
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Builtins allowed in function execution
_SAFE_BUILTINS = {
    # Types
    "True": True,
    "False": False,
    "None": None,
    "int": int,
    "float": float,
    "str": str,
    "bool": bool,
    "list": list,
    "dict": dict,
    "tuple": tuple,
    "set": set,
    "type": type,
    # Math
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sum": sum,
    "len": len,
    "pow": pow,
    # Iteration
    "range": range,
    "enumerate": enumerate,
    "zip": zip,
    "map": map,
    "filter": filter,
    "sorted": sorted,
    "reversed": reversed,
    # Checks
    "isinstance": isinstance,
    "hasattr": hasattr,
    "getattr": getattr,
    "any": any,
    "all": all,
    # String/format
    "format": format,
    "repr": repr,
    "print": print,
    # Exceptions (needed for try/except in user code)
    "Exception": Exception,
    "ValueError": ValueError,
    "TypeError": TypeError,
    "KeyError": KeyError,
    "IndexError": IndexError,
    "ZeroDivisionError": ZeroDivisionError,
}

# Modules injected into the function namespace
_SAFE_MODULES = {
    "math": math,
    "json": json,
    "datetime": datetime,
}


def execute_function(
    function_code: str,
    input_params: dict,
    db: Session,
    thread_id: str | None,
) -> dict:
    """Execute a consolidator Python function.

    The function must define `run(params, call_api, log)` and return the result.

    Args:
        function_code: Python source code containing a `run` function
        input_params: Parameters from the LLM tool call
        db: Database session for API calls
        thread_id: Thread ID for context

    Returns:
        {"success": bool, "data": Any, "_logs": list[str], "error": str | None}
    """
    from app.db.models import ConnectorSpec, ConnectorConfig, Venue
    from app.connectors.spec_executor import execute_spec

    logs: list[str] = []
    api_call_count = 0
    t0 = time.time()

    def log(message: str) -> None:
        """Log a debug message (captured for UI display)."""
        logs.append(str(message))
        logger.info("[fn] %s", message)

    def _do_api_call(
        connector: str, action: str, api_params: dict, use_db: Session
    ) -> tuple[Any, int]:
        """Core API call logic. Returns (payload, duration_ms)."""
        # Look up spec from config DB
        from app.db.engine import _ConfigSessionLocal

        cfg_db = _ConfigSessionLocal()
        try:
            spec = (
                cfg_db.query(ConnectorSpec)
                .filter(ConnectorSpec.connector_name == connector)
                .first()
            )
            if not spec:
                raise ValueError(f"Connector not found: {connector}")

            tool_def = None
            for t in spec.tools or []:
                if isinstance(t, dict) and t.get("action") == action:
                    tool_def = t
                    break
            if not tool_def:
                raise ValueError(f"Tool not found: {connector}.{action}")
        finally:
            cfg_db.close()

        # Resolve venue credentials
        from app.agents.tool_loop import _resolve_venue_config

        venue_lookup = {**input_params, **api_params}
        config_row = _resolve_venue_config(connector, venue_lookup, use_db)
        if not config_row:
            config_row = (
                use_db.query(ConnectorConfig)
                .filter(
                    ConnectorConfig.connector_name == connector,
                    ConnectorConfig.enabled == "true",
                )
                .first()
            )

        credentials = config_row.config if config_row else {}
        venue_id = config_row.venue_id if config_row else None

        # Strip venue params
        clean_params = dict(api_params)
        for k in ("venue", "venue_name", "venue_id"):
            clean_params.pop(k, None)

        # Execute
        call_t0 = time.time()
        result, _ = execute_spec(
            spec,
            tool_def,
            clean_params,
            credentials,
            use_db,
            thread_id,
            venue_id=venue_id,
        )
        call_ms = int((time.time() - call_t0) * 1000)

        payload = result.response_payload

        # Apply response transform
        step_transform = tool_def.get("response_transform")
        if step_transform and step_transform.get("enabled") and payload:
            from app.connectors.response_transform import apply_response_transform

            venue_tz = None
            if venue_id:
                venue_obj = use_db.query(Venue).filter(Venue.id == venue_id).first()
                if venue_obj and venue_obj.timezone:
                    venue_tz = venue_obj.timezone

            wrapped = (
                {"data": payload}
                if isinstance(payload, list)
                else (payload if isinstance(payload, dict) else {"data": payload})
            )
            transformed = apply_response_transform(
                wrapped, step_transform, venue_timezone=venue_tz
            )
            payload = (
                transformed.get("data", transformed)
                if isinstance(transformed, dict)
                else transformed
            )

        if not result.success:
            raise RuntimeError(f"{result.error_message}")

        return payload, call_ms

    def call_api(connector: str, action: str, api_params: dict | None = None) -> Any:
        """Call a connector tool and return the result data."""
        nonlocal api_call_count
        api_call_count += 1

        if api_call_count > 20:
            raise RuntimeError("Too many API calls (max 20)")

        api_params = dict(api_params or {})

        try:
            payload, call_ms = _do_api_call(connector, action, api_params, db)
            log(f"API: {connector}.{action} → {_describe_data(payload)} ({call_ms}ms)")
            return payload
        except Exception as exc:
            log(f"API call {connector}.{action} failed: {exc}")
            return {"error": str(exc)}

    def call_api_parallel(calls: list) -> list:
        """Execute multiple API calls in parallel.

        Args:
            calls: list of (connector, action, params) tuples

        Returns:
            list of results in the same order as the input calls
        """
        nonlocal api_call_count
        api_call_count += len(calls)

        if api_call_count > 20:
            raise RuntimeError("Too many API calls (max 20)")

        from app.db.engine import SessionLocal

        def _worker(call_tuple):
            connector, action, api_params = call_tuple
            api_params = dict(api_params or {})
            worker_db = SessionLocal()
            try:
                payload, call_ms = _do_api_call(
                    connector, action, api_params, worker_db
                )
                return payload, call_ms, None
            except Exception as exc:
                return {"error": str(exc)}, 0, str(exc)
            finally:
                worker_db.close()

        t0_parallel = time.time()
        with ThreadPoolExecutor(max_workers=min(len(calls), 20)) as pool:
            futures = list(pool.map(_worker, calls))
        total_ms = int((time.time() - t0_parallel) * 1000)

        results = []
        for i, (payload, call_ms, err) in enumerate(futures):
            connector, action, _ = calls[i]
            if err:
                log(f"API: {connector}.{action} FAILED: {err}")
            else:
                log(
                    f"API: {connector}.{action} → {_describe_data(payload)} ({call_ms}ms)"
                )
            results.append(payload)

        log(f"Parallel batch: {len(calls)} calls in {total_ms}ms")
        return results

    # Build enriched params with template variables
    try:
        from zoneinfo import ZoneInfo

        now = datetime.datetime.now(ZoneInfo("Pacific/Auckland"))
        offset = now.strftime("%z")
        tz_offset = f"{offset[:3]}:{offset[3:]}"
    except Exception:
        now = datetime.datetime.now(datetime.timezone.utc)
        tz_offset = "+00:00"

    enriched_params = {
        **input_params,
        "today": now.strftime("%Y-%m-%d"),
        "today_iso": now.strftime(f"%Y-%m-%dT00:00:00{tz_offset}").replace("+", "%2B"),
        "one_week_ago": (now - datetime.timedelta(days=7)).strftime("%Y-%m-%d"),
        "one_week_ago_iso": (now - datetime.timedelta(days=7))
        .strftime(f"%Y-%m-%dT00:00:00{tz_offset}")
        .replace("+", "%2B"),
        "four_weeks_ago": (now - datetime.timedelta(days=28)).strftime("%Y-%m-%d"),
        "four_weeks_ago_iso": (now - datetime.timedelta(days=28))
        .strftime(f"%Y-%m-%dT00:00:00{tz_offset}")
        .replace("+", "%2B"),
    }

    # Execute the function
    try:
        namespace: dict[str, Any] = {
            "__builtins__": _SAFE_BUILTINS,
            **_SAFE_MODULES,
        }
        exec(function_code, namespace)

        run_fn = namespace.get("run")
        if not run_fn or not callable(run_fn):
            return {
                "success": False,
                "data": None,
                "_logs": logs,
                "error": "Function must define 'run(params, call_api, log)'",
            }

        # Pass call_api_parallel if function accepts 4 args, otherwise just 3
        import inspect

        sig = inspect.signature(run_fn)
        if len(sig.parameters) >= 4:
            result_data = run_fn(enriched_params, call_api, log, call_api_parallel)
        else:
            result_data = run_fn(enriched_params, call_api, log)
        duration_ms = int((time.time() - t0) * 1000)

        log(f"Completed in {duration_ms}ms ({api_call_count} API calls)")

        return {
            "success": True,
            "data": result_data,
            "_logs": logs,
        }

    except Exception as exc:
        duration_ms = int((time.time() - t0) * 1000)
        logger.exception("Function execution failed after %dms", duration_ms)
        logs.append(f"ERROR: {exc}")
        return {
            "success": False,
            "data": None,
            "_logs": logs,
            "error": str(exc),
        }


def _describe_data(data: Any) -> str:
    """Short description of data for logging."""
    if isinstance(data, list):
        return f"{len(data)} items"
    if isinstance(data, dict):
        if "data" in data and isinstance(data["data"], list):
            return f"{len(data['data'])} items"
        return f"dict with {len(data)} keys"
    if data is None:
        return "null"
    return str(type(data).__name__)
