"""Execute Python functions for consolidator tools.

Functions run in a restricted environment with access to connector APIs
via the `call_api` helper. No file I/O, no imports, no network access
except through `call_api`.
"""

import datetime
import decimal
import json
import logging
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Default ceiling on connector API calls per function execution. A consolidator
# can raise it via consolidator_config.max_api_calls when its workload is
# legitimately larger (e.g. per-invoice fan-out), bounded by _HARD_MAX_API_CALLS.
_DEFAULT_MAX_API_CALLS = 20
_HARD_MAX_API_CALLS = 200

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
    "decimal": decimal,
}


def execute_function(
    function_code: str,
    input_params: dict,
    db: Session,
    thread_id: str | None,
    options: dict | None = None,
) -> dict:
    """Execute a consolidator Python function.

    The function must define `run(params, call_api, log)` and return the result.
    An `extract_document(connector, action, params, schema, instructions)` helper
    is available as a global inside the function for LLM-backed structured
    extraction from binary connector responses (e.g. invoice PDFs).

    Args:
        function_code: Python source code containing a `run` function
        input_params: Parameters from the LLM tool call
        db: Database session for API calls
        thread_id: Thread ID for context
        options: The consolidator_config dict. Honored keys:
            max_api_calls (int, default 20, hard cap 200) and
            allowed_write_actions (list of "connector.action" or bare action
            names allowed to use non-GET methods — default: none).

    Returns:
        {"success": bool, "data": Any, "_logs": list[str], "error": str | None}
    """
    from app.db.models import ConnectorSpec, ConnectorConfig, Venue
    from app.connectors.spec_executor import execute_spec

    options = options or {}
    max_api_calls = min(
        int(options.get("max_api_calls") or _DEFAULT_MAX_API_CALLS),
        _HARD_MAX_API_CALLS,
    )
    allowed_write_actions = set(options.get("allowed_write_actions") or [])

    logs: list[str] = []
    api_call_count = 0
    t0 = time.time()

    def log(message: str) -> None:
        """Log a debug message (captured for UI display)."""
        logs.append(str(message))
        logger.info("[fn] %s", message)

    # Run-local connector-spec cache. A consolidator's parallel batch used to
    # open one config-DB connection per call to re-fetch the SAME spec; with the
    # config DB capped at 25 connections and shared across all environments, a
    # fan-out could exhaust it (the "Config DB unreachable" incident that
    # surfaced to users as a vague "venue" error). Fetch each connector's spec
    # once per run and share it; the lock is held across the cold fetch so a
    # burst of parallel workers waits on one fetch instead of each opening a
    # connection. The spec is expunged so its (column-only) attributes stay
    # readable after the session closes and are safe to read from worker threads.
    _spec_cache: dict[str, Any] = {}
    _spec_cache_lock = threading.Lock()

    def _get_spec(connector: str):
        from app.db.engine import _ConfigSessionLocal

        with _spec_cache_lock:
            if connector in _spec_cache:
                return _spec_cache[connector]
            cfg_db = _ConfigSessionLocal()
            try:
                spec = (
                    cfg_db.query(ConnectorSpec)
                    .filter(ConnectorSpec.connector_name == connector)
                    .first()
                )
                if spec is not None:
                    cfg_db.expunge(spec)
            finally:
                cfg_db.close()
            _spec_cache[connector] = spec
            return spec

    def _do_api_call(
        connector: str, action: str, api_params: dict, use_db: Session
    ) -> tuple[Any, int]:
        """Core API call logic. Returns (payload, duration_ms)."""
        spec = _get_spec(connector)
        if not spec:
            raise ValueError(f"Connector not found: {connector}")

        tool_def = None
        for t in spec.tools or []:
            if isinstance(t, dict) and t.get("action") == action:
                tool_def = t
                break
        if not tool_def:
            raise ValueError(f"Tool not found: {connector}.{action}")

        # Write actions are deny-by-default: a consolidator may only call a
        # non-GET tool when consolidator_config.allowed_write_actions names it.
        method = str(tool_def.get("method", "GET")).upper()
        if method != "GET" and not (
            action in allowed_write_actions
            or f"{connector}.{action}" in allowed_write_actions
        ):
            raise PermissionError(
                f"Write action {connector}.{action} ({method}) is not declared in "
                "consolidator_config.allowed_write_actions"
            )

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

        if api_call_count > max_api_calls:
            raise RuntimeError(f"Too many API calls (max {max_api_calls})")

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

        if api_call_count > max_api_calls:
            raise RuntimeError(f"Too many API calls (max {max_api_calls})")

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

    def extract_document(
        connector: str,
        action: str,
        api_params: dict | None = None,
        schema: dict | None = None,
        instructions: str | None = None,
    ) -> Any:
        """Fetch a binary document via a connector tool and LLM-extract fields.

        The target tool must declare ``response_format: "binary"`` so the
        executor returns ``{content_base64, content_type}``. The document is
        passed to the LLM with ``schema`` (a JSON object describing the fields
        to extract) and the extracted dict is returned. Counts as one API call.
        Returns {"error": ...} on failure — callers must treat that as
        "could not read the document", never as a successful extraction.
        """
        nonlocal api_call_count
        api_call_count += 1
        if api_call_count > max_api_calls:
            raise RuntimeError(f"Too many API calls (max {max_api_calls})")

        try:
            payload, call_ms = _do_api_call(
                connector, action, dict(api_params or {}), db
            )
            if not isinstance(payload, dict) or "content_base64" not in payload:
                raise ValueError(
                    f"{connector}.{action} did not return binary content — "
                    'the tool needs response_format: "binary"'
                )

            from app.interpreter.llm_interpreter import call_llm

            schema_text = json.dumps(schema or {}, indent=1)
            system_prompt = (
                "You extract structured data from a document exactly as printed. "
                "Return ONLY a JSON object matching this schema (no markdown, no "
                f"commentary):\n{schema_text}\n"
                "Rules: copy amounts, quantities and identifiers exactly as they "
                "appear in the document; use null for any field that is not "
                "present or not legible; never guess or compute values."
            )
            user_prompt = (
                instructions or "Extract the fields from the attached document."
            )
            documents = [
                {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": payload.get("content_type", "application/pdf"),
                        "data": payload["content_base64"],
                    },
                }
            ]
            extract_t0 = time.time()
            parsed, _ = call_llm(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                db=db,
                thread_id=thread_id,
                call_type="extraction",
                max_tokens=4096,
                documents=documents,
            )
            total_ms = call_ms + int((time.time() - extract_t0) * 1000)
            log(
                f"extract_document: {connector}.{action} → {_describe_data(parsed)} ({total_ms}ms)"
            )
            return parsed
        except Exception as exc:
            log(f"extract_document {connector}.{action} failed: {exc}")
            return {"error": str(exc)}

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
        "tz_offset": tz_offset.replace("+", "%2B"),
    }

    # Execute the function
    try:
        namespace: dict[str, Any] = {
            "__builtins__": _SAFE_BUILTINS,
            **_SAFE_MODULES,
            "extract_document": extract_document,
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

        # Widen the call to match what the function actually accepts, so older
        # consolidators keep working unchanged:
        #   3 args -> run(params, call_api, log)
        #   4 args -> ... call_api_parallel
        #   5 args -> ... options (its own consolidator_config)
        # Passing options lets one reviewed function_code serve many tools that
        # differ only in configuration — e.g. a date wrapper that reads which
        # action it wraps and what that action calls its date parameters —
        # instead of copying the same logic into a file per tool.
        import inspect

        sig = inspect.signature(run_fn)
        arity = len(sig.parameters)
        if arity >= 5:
            result_data = run_fn(
                enriched_params, call_api, log, call_api_parallel, options
            )
        elif arity >= 4:
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
