"""Shared fakes for the supplier, dojo, sensei and autopilot-outcome tests.

Two things these files used to copy-paste: binding the session factories that
Norm code opens for ITSELF to the test's transaction, and a fake Loaded client
for the dojo's staging and overview paths.
"""

import base64


def bind_sessions(monkeypatch, db_session, *factories: str):
    """Point the session factories Norm code opens for itself at the test.

    Several paths deliberately open their OWN session: the outcome recorder (a
    poisoned session must never turn a completed receive into an error), dojo
    staging and the sensei queue (request config sessions are read-only), the
    job's secret loader. Left unbound they run in a different transaction and
    cannot see the test's uncommitted rows. Worse, ``_ConfigSessionLocal`` is
    the config DB SHARED WITH PRODUCTION: an unbound test reads the real spec
    roster or secrets table, so it passes locally off live data and fails in CI
    against an empty one. That is how
    `test_the_hints_reach_the_training_tool_s_own_guard` went green on this
    machine and red on the first push (27 Aug 2026), and an unbound secrets test
    asserts against a live API key (and prints it into pytest's output).

    The test session joins the fixture's outer transaction in rollback-only
    mode, so a ``commit()`` from the code never escapes the test; ``close()``
    is made a no-op so the code cannot end the fixture's session.

    ``factories`` names the ``app.db.engine`` attributes to bind; both by
    default.
    """
    monkeypatch.setattr(db_session, "close", lambda: None)
    for name in factories or ("SessionLocal", "_ConfigSessionLocal"):
        monkeypatch.setattr(f"app.db.engine.{name}", lambda: db_session)
    return db_session


class FakeLoaded:
    """Loaded, as the dojo's staging and overview paths read it: one invoice
    with a PDF copy, its supplier record's aliases, and the outstanding list.

    ``supplier_id`` is off by default, so staging asks for no aliases unless
    a test says the invoice's supplier record has some.
    """

    def __init__(
        self,
        *,
        supplier="Tamar Farming Company",
        reference="INV-9",
        pdf=b"%PDF-t",
        supplier_id=None,
        aliases=(),
        outstanding=(),
    ):
        self.supplier = supplier
        self.reference = reference
        self.pdf = pdf
        self.supplier_id = supplier_id
        self.aliases = list(aliases)
        self.outstanding = list(outstanding)

    def invoice(self, invoice_id):
        det = {
            "id": invoice_id,
            "supplierName": self.supplier,
            "referenceNumber": self.reference,
            "fileId": "file-1",
        }
        if self.supplier_id:
            det["supplierId"] = self.supplier_id
        return det

    def file_base64(self, file_id):
        return base64.b64encode(self.pdf).decode(), "application/pdf"

    def get(self, path):
        if path.endswith("/aliases"):
            return [{"name": a} for a in self.aliases]
        # The only other read is the overview's outstanding list.
        assert "status=NotReceived" in path, path
        return list(self.outstanding)


def install_fake_loaded(monkeypatch, **kw) -> FakeLoaded:
    """Replace Loaded for the staging helper and the overview endpoint.

    Patched at the SOURCE module: both import ``LoadedInvoiceClient`` inside
    the call, so patching a caller's namespace would miss them.
    """
    fake = FakeLoaded(**kw)
    monkeypatch.setattr(
        "app.services.received_invoice.LoadedInvoiceClient", lambda *a, **k: fake
    )
    return fake
