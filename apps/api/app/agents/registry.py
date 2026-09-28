"""The one Norm agent, and the team-member slugs it answers for.

Team members are packaging, not agents: hiring one switches its Apps on, App
pages join its sidebar section, and an automated task created under one keeps
that member's tool scope. Every one of them — and ``norm``, the domain new
threads are filed under — is answered by the same NormAgent.
"""

from app.agents.base import BaseDomainAgent
from app.agents.norm import NORM_DOMAIN, NormAgent

#: Team members a thread, task, playbook or App page can belong to.
MEMBERS = (
    "procurement",
    "hr",
    "reports",
    "time_attendance",
    "marketing",
    "executive_chef",
    "app_builder",
)

_AGENT = NormAgent()


def get_agent(domain: str | None) -> BaseDomainAgent | None:
    """The Norm agent for ``norm`` or any team member; None for anything else
    (a retired slug, "meta", "unknown") so callers can refuse it."""
    if domain == NORM_DOMAIN or domain in MEMBERS:
        return _AGENT
    return None


def norm_agent() -> BaseDomainAgent:
    """The Norm agent, for callers that don't start from a slug."""
    return _AGENT


def registered_domains() -> list[str]:
    """Team-member slugs (App pages and tasks belong to one of these)."""
    return list(MEMBERS)
