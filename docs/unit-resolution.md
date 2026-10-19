# How Norm decides a product's unit (self-healing units)

*Last reworked 19 Oct 2026 — never Loaded's unit, Norm's answer at any confidence, the daily unit check; 14 Oct
2026 — no person-confirmed tier; 28 Aug 2026 — "analyser on top". If you are about to change how a product's
delivered unit is chosen, read this first.*

## The rule: never the unit Loaded stores

The owner, 18 Oct 2026: *"i've been working hard to tell agents not to rely on loaded at all."* The unit Loaded
stores for a supplier product (the `unitId` on an item's supplier record, the "variant") is whatever the last
receive used, by anyone. It was wrong systematically: basil bunches booked as Kilo at Somewhere North (73 lines),
whole salami as 1 Kilo (17), loaves by weight. A review that judged lines against Loaded's set-up missed all of
them, because every line was wrong the same way.

So the unit comes from **the invoice and Norm's own knowledge, only**. Nothing in the receive path reads the
supplier record's unit, the unit on Loaded's draft line, Loaded's stocktake unit or Loaded's price history to
decide or confirm a unit. Do not add it back as a fallback, a tie-breaker or a "last resort". When Norm has no
answer, the line has no unit, and the venue's unit chooser (`unit_guess`) picks from the venue's units by the
invoice's own words.

## The problem this solves

Every invoice line needs a **delivered unit** — the physical size of one item
("700 mL", "Kilo", "12 pack") — so Loaded can cost recipes and track stock. The
AI that reads an invoice can produce a plausible-but-wrong unit. The classic
case: `1.0 NRB X1 BACARDI CARTA BLANCA RUM 37.5` (a single bottle of rum, **no
size printed** — `37.5` is the alcohol %, `X1` a quantity) was read as "1 pack" /
"each". That is a **dud**: a rum is poured, so it is a volume, not a count.

Receiving is meant to run on **autopilot at scale**, so there is no human
correcting units. Norm has to catch and fix its own dud units.

## The sources, and the order between them

Norm keeps a shared, cross-customer memory of each product's unit
(`supplier_products`, keyed by supplier + code). A row's unit can come from two
ranked sources, and the order is what matters:

1. **The resolver's verdict** (`enriched`) — the analyser worked the unit out
   from evidence. The answer.
2. **The AI's raw read** (`printed`) — what the extraction read off an invoice.
   **Provisional only.**

The rank lives in `supplier_catalog._RANK` (`{"enriched": 1, "printed": 0}`). A
verdict outranks a read, so a resolver answer can never be buried by a poisoned
read. The change on 28 Aug 2026 was lifting `enriched` **above** `printed` (it
used to sit below).

**There is no person-confirmed tier** (removed 14 Oct 2026). It used to sit on
top — an admin's dojo baseline (`human`), 74 products. The user: *"the human
does not add any value in us getting to autopilot across 1000s of venues with
100s of invoices each per month."* The numbers agree: on products whose size
isn't in the name, the six Cook Brothers venues' own Loaded set-ups were right
about 85% of the time, and Norm with web search 93% (unit-size test, 14 Oct
2026). Those 74 rows were demoted the same day (69 already carried Norm's own,
matching verdict; 5 are re-checked as reads). Nothing can write the tier back:
`observe_extraction` only records reads, `SupplierProduct` refuses any other
provenance, and `test_supplier_catalog.TestThereIsNoPersonConfirmedTier` fails
if it returns.

One more source is kept as evidence but **never ranks**:

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
   **unit estimator** (`unit_resolver.py`). Lines that already carry a verdict
   are used as-is (no model call).
3. **The estimator (14 Oct 2026).** One call per line, in parallel: Opus with
   web search estimates the real size of ONE delivered unit like a purchasing
   manager — identify the product, find the formats it is actually sold in
   (this supplier's own listing first), read the supplier's price level off the
   rest of the invoice, price each real format against it. **Code, not the
   model, turns the size into a venue unit** (`match_unit`: same type, same
   amount — '1 kg' → Kilo, never '1 L' → Each); a size the venue lacks becomes a
   unit to create (`unit_name_for`, gated by `auto_create_units`). Answers are
   cached per invoice line, so re-opening a card costs nothing. Measured on 45
   products whose size the invoice didn't print: **93% exact**, against ~85% for
   the six Cook Brothers venues' own Loaded set-ups of the same products. It
   replaced one batched choose-a-unit-id call (65% on the same test).
4. **Only HIGH is a verdict.** `supplier_catalog.learn_from_resolver` records a
   HIGH answer as `enriched` (high was right 65 of 66 times). Medium (17 of 22),
   low, or no answer is recorded as a **guess** (`evidence['guess']`, stamped with
   the estimator's version so a guess made under older rules is never reused).
5. **Norm's answer decides, however sure it is** (`ESTIMATE_DECIDES` in
   `invoice_replica.py`). An unsure guess is reused for 30 days
   (`supplier_catalog.earlier_estimate`), so every invoice books the product the
   same way and the estimate isn't paid for again, then worked out afresh.
6. **A person settles the unsure ones, once.** The daily unit check
   (`services/unit_check.py`, the Dojo's "Daily unit check") lists every line
   received on an answer Norm wasn't sure of. Confirming or changing it writes
   the person's answer as Norm's verdict (`supplier_catalog.record_answer`), so
   the product is never asked about again. The same check proposes corrections
   where a booked unit disagrees with the invoice or the catalogue.
7. **Products with no code are learned too.** The catalogue keys a product by
   supplier + stock code, else by its normalised wording (`product_key`) —
   Factory Group prints no codes at all (0 of 27 lines on INV-0446).
8. **Evidence, not practice.** The estimator sees the invoice and Norm's own
   catalogue facts about the same product from other suppliers; it is NOT told
   how a venue happens to stock the item.

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
- The verdict is **sticky**: once a HIGH verdict is stored it is trusted and not
  re-run. A product whose packaging genuinely changes would need its
  verdict cleared. ("Most-recent-wins within the top tier" is the open
  alternative.)

## Where it lives (code pointers)

- **Don't-invent prompt:** `app/services/invoice_extraction.py`
  (`BUILTIN_MAIN_PROMPT`) + the live "Main prompt" `supplier_invoice_specs` row.
- **Ranking + verdicts + confidence:** `app/services/supplier_catalog.py` —
  `_RANK`, `_recompute_current`, `apply_enrichment`, `learn_from_resolver`,
  `catalog_unit_for_line`.
- **Receive-path unit resolution:** `app/services/invoice_replica.py`
  (the per-line unit block in `build_replica`) — walks the page's size → Norm's
  catalogue verdict → Norm's earlier unsure estimate → the printed charge word
  (a measurement; never a sizeless pack count like "6 PK", never an Ordermentum
  line's unit, which is Loaded's) → the estimator. Then
  `replica_finish.settle_units`: the venue's unit chooser for a line still
  without a unit.
- **The daily unit check:** `app/services/unit_check.py`, the Dojo endpoints in
  `app/routers/supplier_spec_dojo.py`, `apps/web/app/components/settings/UnitCheckPanel.tsx`.
- **The estimator:** `app/services/unit_resolver.py` (prompt, `match_unit`,
  `unit_name_for`); web search runs through `call_llm(tools=…, effort=…)`.
- **Tests:** `tests/test_supplier_catalog.py`, `tests/test_invoice_replica.py`
  (`TestCatalogueTierAndResolver`, `TestAGuessIsCheckedAgain`,
  `TestNormSizesALineWithNoSize`), `tests/test_unit_resolver.py`,
  `tests/test_unit_check.py`, `tests/test_every_blocker_has_a_fix.py`.
