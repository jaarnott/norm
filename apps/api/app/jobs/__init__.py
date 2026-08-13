"""Entrypoints for work that runs OUTSIDE the web process.

A Cloud Run Job runs the same image with a different command, so anything in
here must be reachable as ``python -m app.jobs.<name>``. Note the Dockerfile
copies only ``app/`` (plus alembic) into the runtime image — ``scripts/`` is
not there, which is why these live under the package rather than beside the
other operational scripts.
"""
