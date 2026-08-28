# How Norm decides a product's unit (self-healing units)

*Last reworked 28 Aug 2026 — "analyser on top". If you are about to change how a
product's delivered unit is chosen, read this first.*

## The problem this solves

Every invoice line needs a **delivered unit** — the physical size of one item
("700 mL", "Kilo", "12 pack") — so Loaded can cost recipes and track stock. The
AI that reads an invoice can produce a plausible-but-wrong unit. The classic
case: `1.0 NRB X1 BACARDI CARTA BLANCA RUM 37.5` (a single bottle of rum, **no
size printed** — `37.5` is the alcohol %, `X1` a quantity) was read as "1 pack" /
"each". That is a **dud**: a rum is poured, so it is a volume, not a count.

Receiving is meant to run on **autopilot at scale**, so there is no human
correcting units. Norm has to catch and fix its own dud units.

## The two sources, and the order between them

Norm keeps a shared, cross-customer memory of each product's unit
(`supplier_products`, keyed by supplier + code). A row's unit can come from two
sources, and the order is what matters:

1. **The resolver's verdict** (`enriched`) — the analyser worked the unit out
   from evidence. **This is the authority.**
2. **The AI's raw read** (`printed`) — what the extraction read off an invoice.
   **Provisional only.**

The rank lives in `supplier_catalog._RANK` (`{"enriched": 1, "printed": 0}`). A
verdict outranks a read, so a resolver answer can never be buried by a poisoned
read. Two sources were **removed** as ranking inputs (28 Aug 2026):

- **`human`** — a reserved top tier that nothing ever wrote. Dropped.
- **`practice`** (what a receive actually used) — kept as evidence for the
  hygiene report, but **never a ranking source**: a receive can carry a user's
  mistake, and once receiving is automated a receive just echoes Norm's own
  decision, so it must not overrule a read or a verdict.

**Consequence — a raw read is never handed back as "the answer".**
`catalog_unit_for_line` only speaks for a resolver verdict; a `printed` row
returns `None`, so the receive path routes the line to the resolver instead of
receiving a provisional (possibly poisoned) unit.

## The self-healing loop

1. **Extraction never invents a unit.** The main prompt (`BUILTIN_MAIN_PROMPT` in
   `invoice_extraction.py`, mirrored in the live "Main prompt" config row) only
   gives a `unit_of_measure` when a real size is printed. Quantity/packaging
   notation — `NRB X1`, a bare bottle/each count, an ABV like `37.5` — is not a
   size, so a sizeless spirit/wine returns **null** rather than a made-up count.
2. **A read is provisional; a verdict is trusted.** At receive, a line whose only
   answer would be a raw read (or a poisoned catalogue read) is put through the
   **unit resolver** (`unit_resolver.py`) — the batched call built to work a unit
   out. Lines that already carry a resolver verdict are used as-is (no model
   call).
3. **The verdict is remembered, with its confidence.**
   `supplier_catalog.learn_from_resolver` records HIGH and MEDIUM verdicts as
   `enriched` (with the confidence); **LOW is left unrecorded so the line is put
   through the resolver again on its next sighting.** Because the memory is
   shared across all customers, a unit resolved once — by any venue — is
   available to everyone, and a low-confidence answer improves as more evidence
   (siblings, a bigger venue) arrives.
4. **The resolver leans on world knowledge, not the venue's Loaded catalogue.**
   Its confidence comes mainly from sibling lines on the same invoice and
   product/world knowledge (a rum is a volume, lemons sell by the kilo); the
   venue's own stock list is a minor input, so a brand-new venue with an empty
   Loaded catalogue still resolves confidently.

No manual cleanup list, no reliance on a human receive: a poisoned row corrects
itself the next time the product is seen anywhere.

## Worked example — Bacardi Carta Blanca Rum

For code `989303`, the memory row held three sightings:

| source | value | where from |
|---|---|---|
| `printed` | **"1 pack"** | the extraction's read of an earlier invoice (`0388565608`) — the same misread, cached |
| `enriched` | "1L" | the resolver worked out "a single 1-litre bottle of rum" |
| `practice` | "Litre" | the user's own receive of invoice `0388794402` |

Under the **old** rank (`printed > enriched > practice`) the misread "1 pack" won
over both the correct verdict and the user's own receive. Under the **new** rank,
the `enriched` "1L" outranks the `printed` "1 pack": the row heals to 1L, and had
there been no verdict, the poisoned read would not answer at all and the line
would go to the resolver.

`printed` is the extraction's *derived* `unit_of_measure`, **not** literal invoice
text (`supplier_catalog.observe_extraction`) — "1 pack" was the model's reading of
`1.0 NRB X1`, not something the invoice printed. So the "dirty data" was the
extraction bug, cached; fixing extraction (step 1) plus the rank flip (the row
heals) settles it.

## Scope / known limits

- A **clear read that is wrong for the product** — e.g. a wine invoice that prints
  only `12PK` (no ml), read as "12 pack" — is **not** re-checked on the invoice
  that carries it (clear reads are not sent to the resolver). It is caught only if
  the line reaches the resolver via the catalogue (no verified verdict yet). This
  is a deliberate cost/latency trade; revisit if such reads prove common.
- The verdict is **sticky**: once a high/medium verdict is stored it is trusted
  and not re-run. A product whose packaging genuinely changes would need its
  verdict cleared. ("Most-recent-wins within the top tier" is the open
  alternative.)

## Where it lives (code pointers)

- **Don't-invent prompt:** `app/services/invoice_extraction.py`
  (`BUILTIN_MAIN_PROMPT`) + the live "Main prompt" `supplier_invoice_specs` row.
- **Ranking + verdicts + confidence:** `app/services/supplier_catalog.py` —
  `_RANK`, `_recompute_current`, `apply_enrichment`, `learn_from_resolver`,
  `catalog_unit_for_line`.
- **Receive-path unit resolution:** `app/services/invoice_replica.py`
  (the per-line unit block, ~lines 641-1017) — walks variant → page read →
  catalogue verdict → printed column → resolver.
- **The resolver:** `app/services/unit_resolver.py`.
- **Tests:** `tests/test_supplier_catalog.py`, `tests/test_invoice_replica.py`
  (`TestCatalogueTierAndResolver`), `tests/test_unit_resolver.py`.
