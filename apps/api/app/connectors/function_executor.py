"""Execute Python functions for consolidator tools.

Functions run in a restricted environment with access to connector APIs
via the `call_api` helper. No file I/O, no imports, no network access
except through `call_api`.
"""

import ast
import datetime
import decimal
import hashlib
import json
import logging
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def _extraction_cache_key(
    connector: str, action: str, api_params: dict, schema: dict, instructions: str
) -> str:
    """Stable hash of everything that determines an extraction's output.

    The source document is immutable (a file id in api_params), and the schema
    / instructions decide what is pulled from it — so identical inputs always
    yield the same fields and can be cached. A different schema for the same
    file hashes differently, so the two extraction shapes never collide.
    """
    material = json.dumps(
        {
            "connector": connector,
            "action": action,
            "params": api_params or {},
            "schema": schema or {},
            "instructions": instructions or "",
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(material.encode()).hexdigest()


def _extraction_cache_get(db: Session, cache_key: str) -> Any | None:
    """Return a cached extraction, or None.

    Uses the caller's session deliberately: it keeps the cache inside whatever
    transaction the request is already running, so tests (which roll their
    session back) never see or leave behind cached rows.
    """
    from app.db.models import DocumentExtraction

    try:
        row = (
            db.query(DocumentExtraction)
            .filter(DocumentExtraction.cache_key == cache_key)
            .first()
        )
        return row.data if row else None
    except Exception as exc:  # noqa: BLE001 — cache must never break extraction
        logger.warning("extraction cache read failed: %s", exc)
        return None


def _extraction_cache_put(
    db: Session, cache_key: str, connector: str, action: str, data: Any
) -> None:
    """Store an extraction, committed with the caller's transaction.

    Written inside a SAVEPOINT so a duplicate key (another run stored the same
    document first) rolls back only this insert, never the caller's work.
    """
    from app.db.models import DocumentExtraction

    try:
        with db.begin_nested():
            db.add(
                DocumentExtraction(
                    cache_key=cache_key, connector=connector, action=action, data=data
                )
            )
    except Exception as exc:  # noqa: BLE001 — cache write is best-effort
        logger.debug("extraction cache write skipped: %s", exc)


# Default ceiling on connector API calls per function execution. A consolidator
# can raise it via consolidator_config.max_api_calls when its workload is
# legitimately larger (e.g. per-invoice fan-out), bounded by _HARD_MAX_API_CALLS.
_DEFAULT_MAX_API_CALLS = 20
_HARD_MAX_API_CALLS = 200


# Builtins allowed in function execution
def _safe_getattr(obj, name, *default):
    """`getattr` that cannot be used to reach private attributes.

    The static guard below refuses `x.__class__` in the source, so without
    this a caller would just write `getattr(x, "__class__")` instead.
    """
    if isinstance(name, str) and name.startswith("_"):
        raise ValueError(f"attribute '{name}' is not available in this sandbox")
    return getattr(obj, name, *default)


#: Constructs that turn this sandbox from a restriction into a suggestion.
#:
#: The namespace handed to ``exec`` necessarily contains real objects — the
#: json/math/datetime/decimal modules, and the very functions the code is meant
#: to call. Python lets you walk from ANY of them to the interpreter:
#: ``json.__builtins__`` hands back the genuine builtins (so ``__import__`` and
#: therefore ``os``, the DB engine, every venue's stored credentials), and
#: ``().__class__.__base__.__subclasses__()`` reaches 644 live classes. Both
#: were verified working before this guard existed.
#:
#: Restricting the namespace cannot fix that — the escape routes are attributes
#: of objects the code legitimately holds. So the source is parsed and refused
#: instead: no imports, and no private/dunder attribute access. Real
#: consolidators use leading-underscore *local names* (`_rows_of`,
#: `_summarise`) freely, and those are untouched — it is attribute access that
#: is blocked.
#:
#: This is hardening, not a boundary. The durable fix is running untrusted
#: logic out-of-process with no database credentials and no app package on
#: sys.path; this buys time for that.
def _reject_unsafe_source(function_code: str) -> None:
    try:
        tree = ast.parse(function_code)
    except SyntaxError as exc:
        raise ValueError(f"could not parse function: {exc}") from exc

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            raise ValueError(
                "imports are not available in this sandbox — the data you need "
                "comes through call_api/store"
            )
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise ValueError(
                f"attribute '{node.attr}' is not available in this sandbox"
            )


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
    "getattr": _safe_getattr,
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
    call_api_override=None,
    storage_override=None,
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
        call_api_override: Replaces the `call_api` the sandbox hands the
            function. Used by the app platform, whose door
            (services/app_runtime.call_action) enforces a per-version allowlist
            and the viewer's own permissions — WITHOUT this, an app's logic
            would reach the connector layer directly and the sandbox would be
            the way around its own app's declared reach. Still counted against
            max_api_calls.

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

        # In-process handlers run here, not over HTTP. Mirrors step 3 of
        # tool_executor.execute_connector_tool, which warns that without this
        # lookup "every @register'd internal tool (resolve_dates,
        # create_purchase_order, list_automated_tasks, ...) falls through to
        # execute_spec and fails". The sandbox never had it, so a consolidator
        # calling an internal tool built a request against a spec with no
        # base_url and failed with "Request URL is missing an 'http://' or
        # 'https://' protocol" — which reads like a misconfigured connector
        # rather than a call that was never routable.
        #
        # Deliberately placed AFTER the write-action gate above, so an internal
        # write (create_purchase_order) still has to be declared in
        # allowed_write_actions. Venue params are NOT stripped the way they are
        # for the HTTP path below: resolve_dates reads day_start_time and
        # timezone off venue_id, and without it silently applies the org
        # default instead of the venue's own trading day.
        from app.agents.internal_tools import get_handler

        handler = get_handler(connector, action)
        if handler:
            call_t0 = time.time()
            handler_result = handler(dict(api_params), use_db, thread_id)
            call_ms = int((time.time() - call_t0) * 1000)
            if not handler_result.get("success", True):
                raise RuntimeError(
                    handler_result.get("error") or f"{connector}.{action} failed"
                )
            return handler_result.get("data"), call_ms

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

    def _extract_uncached(
        connector: str,
        action: str,
        api_params: dict,
        schema: dict | None,
        instructions: str | None,
        cache_key: str,
        session: Session,
    ) -> Any:
        """Download + LLM-extract + cache-put on the given session.

        Shared core of extract_document (turn session) and
        extract_documents_parallel (per-worker sessions). No cache read and no
        budget accounting here — callers own both. Raises on failure.
        """
        payload, call_ms = _do_api_call(connector, action, api_params, session)
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
        user_prompt = instructions or "Extract the fields from the attached document."
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
            db=session,
            thread_id=thread_id,
            call_type="extraction",
            max_tokens=4096,
            documents=documents,
        )
        total_ms = call_ms + int((time.time() - extract_t0) * 1000)
        log(
            f"extract_document: {connector}.{action} → {_describe_data(parsed)} ({total_ms}ms)"
        )
        # Cache only clean extractions — never an error dict, and never a
        # result with no usable fields (a transient read failure).
        if (
            isinstance(parsed, dict)
            and "error" not in parsed
            and any(v is not None for v in parsed.values())
        ):
            _extraction_cache_put(session, cache_key, connector, action, parsed)
        return parsed

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
        # Cache hit: return the stored extraction without downloading the
        # document or calling the LLM — and without spending an API call, since
        # neither the fetch nor the extraction runs.
        cache_key = _extraction_cache_key(
            connector, action, api_params or {}, schema or {}, instructions or ""
        )
        cached = _extraction_cache_get(db, cache_key)
        if cached is not None:
            log(f"extract_document: {connector}.{action} → cache hit")
            return cached

        nonlocal api_call_count
        api_call_count += 1
        if api_call_count > max_api_calls:
            raise RuntimeError(f"Too many API calls (max {max_api_calls})")

        try:
            return _extract_uncached(
                connector,
                action,
                dict(api_params or {}),
                schema,
                instructions,
                cache_key,
                db,
            )
        except Exception as exc:
            log(f"extract_document {connector}.{action} failed: {exc}")
            return {"error": str(exc)}

    def extract_documents_parallel(requests: list) -> list:
        """Extract many documents concurrently (rolling window of 10).

        Each request is a dict: {"connector", "action", "params", "schema",
        "instructions"} — the same arguments extract_document takes. Returns
        one result per request, in request order: the parsed dict, or
        {"error": ...} for that document alone.

        Cache hits are answered first from the turn's session and spend no
        API-call budget; only the misses fan out. Each worker runs on its OWN
        session and COMMITS it, so a finished extraction (its cache row and
        LLM-call record) is durable immediately and one bad document never
        poisons the batch. Requires the turn's thread to be committed — which
        the message flow now guarantees — or the workers' rows would violate
        the thread foreign key.
        """
        requests = requests or []
        results: list[Any] = [None] * len(requests)
        pending: list[tuple[int, dict, str]] = []
        for i, r in enumerate(requests):
            r = r if isinstance(r, dict) else {}
            key = _extraction_cache_key(
                r.get("connector") or "",
                r.get("action") or "",
                r.get("params") or {},
                r.get("schema") or {},
                r.get("instructions") or "",
            )
            cached = _extraction_cache_get(db, key)
            if cached is not None:
                results[i] = cached
            else:
                pending.append((i, r, key))

        if not pending:
            if requests:
                log(f"Parallel extraction: all {len(requests)} cached")
            return results

        nonlocal api_call_count
        api_call_count += len(pending)
        if api_call_count > max_api_calls:
            raise RuntimeError(f"Too many API calls (max {max_api_calls})")

        from app.db.engine import SessionLocal

        def _worker(item):
            i, r, key = item
            worker_db = SessionLocal()
            try:
                parsed = _extract_uncached(
                    r.get("connector") or "",
                    r.get("action") or "",
                    dict(r.get("params") or {}),
                    r.get("schema"),
                    r.get("instructions"),
                    key,
                    worker_db,
                )
                worker_db.commit()
                return i, parsed
            except Exception as exc:
                worker_db.rollback()
                log(
                    f"extract_document {r.get('connector')}.{r.get('action')} failed: {exc}"
                )
                return i, {"error": str(exc)}
            finally:
                worker_db.close()

        t0_batch = time.time()
        with ThreadPoolExecutor(max_workers=min(len(pending), 10)) as pool:
            for i, parsed in pool.map(_worker, pending):
                results[i] = parsed
        log(
            f"Parallel extraction: {len(pending)} documents in "
            f"{int((time.time() - t0_batch) * 1000)}ms "
            f"({len(requests) - len(pending)} cache hits)"
        )
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
        "tz_offset": tz_offset.replace("+", "%2B"),
    }

    if call_api_override is not None:
        _inner_call = call_api_override

        def call_api(connector: str, action: str, api_params: dict | None = None):  # type: ignore[misc]
            nonlocal api_call_count
            api_call_count += 1
            if api_call_count > max_api_calls:
                raise RuntimeError(f"Too many API calls (max {max_api_calls})")
            try:
                payload = _inner_call(connector, action, dict(api_params or {}))
                log(f"API: {connector}.{action} → {_describe_data(payload)}")
                return payload
            except Exception as exc:
                # Same shape as the default call_api: a refused call is data the
                # function can react to, not an exception that kills the run.
                log(f"API call {connector}.{action} failed: {exc}")
                return {"error": str(exc)}

        # EVERY route to the network must go through the override, not just the
        # one named `call_api`. The sandbox widens run() by arity, so logic
        # declaring a 4th parameter is handed `call_api_parallel` — which used
        # to reach `_do_api_call` directly, resolving venue credentials itself
        # and skipping the caller's allowlist, permission and audit checks.
        # Naming a fourth parameter was therefore enough to read any action on
        # any connector, unaudited. It now fans out over the same door.
        #
        # Sequential on purpose: the door writes an audit row per call using
        # the caller's request-scoped session, which is not safe to share
        # across threads. Callers that need real concurrency should widen the
        # door, not step around it.
        def call_api_parallel(calls: list) -> list:  # type: ignore[misc]
            out = []
            for call_tuple in calls or []:
                connector, action, api_params = call_tuple
                out.append(call_api(connector, action, api_params))
            log(f"Batch: {len(calls or [])} calls through the caller's door")
            return out

    # Execute the function
    try:
        namespace: dict[str, Any] = {
            "__builtins__": _SAFE_BUILTINS,
            **_SAFE_MODULES,
        }
        if call_api_override is None:
            # Document extraction also calls out directly, and a caller that
            # supplied its own door has no way to authorize it — so it is
            # absent rather than ungoverned. Consolidators (no override) keep
            # it unchanged.
            namespace["extract_document"] = extract_document
            namespace["extract_documents_parallel"] = extract_documents_parallel
        if storage_override is not None:
            # A NAMESPACE global rather than another positional argument:
            # arity 4 and 5 already mean call_api_parallel and options, so
            # adding a 6th would be invisible to anything written today and
            # ambiguous for anything written tomorrow. `store` is only present
            # when the caller supplied a door for it.
            namespace["store"] = storage_override
        # Refuse the source BEFORE running it. At exec time rather than at
        # save time, so logic stored before this guard existed is covered too.
        _reject_unsafe_source(function_code)
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
