"""Email template rendering using Jinja2."""

import logging
from pathlib import Path

from jinja2.sandbox import SandboxedEnvironment
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).parent.parent / "templates" / "email"

_jinja_env = SandboxedEnvironment(autoescape=True)


def render_template(
    template_name: str,
    context: dict,
    db: Session | None = None,
) -> tuple[str, str]:
    """Render an email template. Returns (subject, html_body).

    Tries DB-stored template first, falls back to file-based template.
    """
    # Try DB template
    if db:
        from app.db.models import EmailTemplate

        tpl = (
            db.query(EmailTemplate).filter(EmailTemplate.name == template_name).first()
        )
        if tpl:
            subject = _jinja_env.from_string(tpl.subject_template).render(**context)
            html = _jinja_env.from_string(tpl.html_template).render(**context)
            return subject, html

    # Fall back to file template
    subject_file = TEMPLATE_DIR / f"{template_name}_subject.txt"
    html_file = TEMPLATE_DIR / f"{template_name}.html"

    if not html_file.exists():
        logger.warning("Email template not found: %s", template_name)
        # Generate a simple fallback
        subject = context.get("subject", template_name.replace("_", " ").title())
        html = f"<p>{context.get('message', 'No template found.')}</p>"
        return subject, html

    subject = ""
    if subject_file.exists():
        subject = _jinja_env.from_string(subject_file.read_text()).render(**context)
    else:
        subject = context.get("subject", template_name.replace("_", " ").title())

    html = _jinja_env.from_string(html_file.read_text()).render(**context)
    return subject, html
