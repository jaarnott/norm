# ruff: noqa: F821 — sandbox-injected names; not imports.
#
# Canonical function_code for the `xero.get_contacts` consolidator — suppliers
# and customers in the venue's Xero organisation. The xero_common helpers are
# prepended by scripts/sync_xero_connector.py.
#
# Xero pages contacts 100 at a time; a name query uses searchTerm (server-side,
# matches name / email / contact number). Without a query, up to 3 pages.
#
# Requires consolidator_config: {"max_api_calls": 4, "allowed_write_actions": []}


def run(params, call_api, log):
    query = str(params.get("query") or "").strip()
    kind = str(params.get("kind") or "supplier").strip().lower()
    limit = int(params.get("limit") or 100)
    include_archived = str(params.get("include_archived") or "").lower() in (
        "true",
        "1",
        "yes",
    )
    vp = xero_venue_params(params)
    settings, err = xero_settings(call_api, params)
    if err:
        return {"error": err}

    contacts = []
    page = 1
    max_pages = 1 if query else 3
    while page <= max_pages:
        q = {**vp, "page": str(page)}
        if query:
            q["search_term"] = query
        if include_archived:
            q["include_archived"] = "true"
        payload = call_api("xero", "list_contacts", q)
        rows, err = xero_rows(payload, "Contacts")
        if err:
            return {"error": f"Xero contacts unavailable: {err}"}
        for c in rows:
            is_supplier = bool(c.get("IsSupplier"))
            is_customer = bool(c.get("IsCustomer"))
            if kind == "supplier" and not is_supplier:
                continue
            if kind == "customer" and not is_customer:
                continue
            phones = [
                p
                for p in (c.get("Phones") or [])
                if isinstance(p, dict) and p.get("PhoneNumber")
            ]
            phone = None
            if phones:
                p = phones[0]
                phone = " ".join(
                    str(p.get(k) or "")
                    for k in ("PhoneCountryCode", "PhoneAreaCode", "PhoneNumber")
                ).strip()
            contacts.append(
                {
                    "contact_id": c.get("ContactID"),
                    "name": c.get("Name"),
                    "email": c.get("EmailAddress") or None,
                    "phone": phone,
                    "tax_number": c.get("TaxNumber"),
                    "is_supplier": is_supplier,
                    "is_customer": is_customer,
                    "status": c.get("ContactStatus"),
                }
            )
        if len(rows) < 100:
            break
        page += 1

    contacts.sort(key=lambda c: str(c.get("name") or "").lower())
    return {
        "organisation": settings.get("organisation"),
        "query": query or None,
        "kind": kind,
        "contacts": contacts[:limit],
        "count": len(contacts),
        "more_available": len(contacts) > limit or (not query and page > max_pages),
    }
