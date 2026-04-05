"""Agent registry — maps domain slugs to agent instances."""

from app.agents.base import BaseDomainAgent

_agents: dict[str, BaseDomainAgent] = {}


def _init():
    if _agents:
        return

    from app.agents.procurement.agent import ProcurementAgent
    from app.agents.hr.agent import HrAgent
    from app.agents.reports.agent import ReportsAgent
    from app.agents.time_attendance.agent import TimeAttendanceAgent

    for agent in [ProcurementAgent(), HrAgent(), ReportsAgent(), TimeAttendanceAgent()]:
        _agents[agent.domain] = agent


def get_agent(domain: str) -> BaseDomainAgent | None:
    """Return the agent for the given domain slug, or None."""
    _init()
    return _agents.get(domain)


def registered_domains() -> list[str]:
    """Return all registered domain slugs."""
    _init()
    return list(_agents.keys())
