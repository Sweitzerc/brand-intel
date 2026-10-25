"""
Scheduled monitoring: scan, compare with last time, and email what's new.

Run by hand or from a timer (the Raspberry Pi installer sets one up):

    python -m brand_intel.monitor            # scan + email alerts
    python -m brand_intel.monitor --dry-run  # print the report, send nothing

Checks, all configured under `alerts:` in config.yaml:
  - overall tone for the brand fell below `tone_below`
  - new negative mentions (not seen in any earlier scan)
  - newly registered lookalike domains for your own domain(s)

Email is sent with the SMTP_* settings from the environment (see .env.example).
"""

import argparse
import os
import smtplib
from email.message import EmailMessage

from . import storage
from .collectors import COLLECTORS
from .core import load_config
from .investigators import lookalikes
from .pipeline import run_scan

NEGATIVE = -0.5


def check_mentions(query: str, cfg: dict) -> list[str]:
    known = storage.known_urls(query)
    scan = run_scan(query, list(COLLECTORS), limit=50)
    lines = []
    threshold = cfg.get("tone_below", -0.2)
    if scan.items and scan.avg < threshold:
        lines.append(f"Overall tone for {query} is {scan.avg:+.2f} across {len(scan.items)} mentions "
                     f"(alert level {threshold:+.2f}).")
    fresh = [i for i in scan.items if (i.score or 0) <= NEGATIVE and i.url not in known]
    for i in sorted(fresh, key=lambda i: -i.engagement)[:10]:
        lines.append(f"New negative mention ({i.score:+.2f}, {i.platform}, {i.source}): {i.title}\n  {i.url}")
    if scan.errors:
        lines.append("Sources skipped: " + "; ".join(scan.errors))
    return lines


def check_lookalikes(domain: str, tlds: list[str]) -> list[str]:
    registered = {d for d, _, ip in lookalikes.check_domains(domain, tlds) if ip}
    new = storage.record_domains(domain, registered)
    return [f"Lookalike domain registered: {d}" for d in sorted(new)]


def send_email(subject: str, body: str) -> None:
    host, to = os.environ.get("SMTP_HOST"), os.environ.get("ALERT_TO")
    if not (host and to):
        raise RuntimeError("SMTP_HOST and ALERT_TO must be set to send email")
    msg = EmailMessage()
    msg["Subject"], msg["To"] = subject, to
    msg["From"] = os.environ.get("SMTP_USER", to)
    msg.set_content(body)
    with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", 587)), timeout=30) as smtp:
        smtp.starttls()
        if os.environ.get("SMTP_USER"):
            smtp.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASSWORD", ""))
        smtp.send_message(msg)


def run(dry_run: bool = False) -> str:
    cfg = load_config()
    alerts = cfg.get("alerts", {})
    sections = []
    for query in [cfg.get("brand"), *(cfg.get("competitors", []) if alerts.get("watch_competitors") else [])]:
        if query:
            lines = check_mentions(query, alerts)
            if lines:
                sections.append(f"== {query} ==\n" + "\n".join(lines))
    if alerts.get("lookalikes", True):
        for domain in cfg.get("domains", []):
            lines = check_lookalikes(domain, cfg.get("tlds", ["com"]))
            if lines:
                sections.append(f"== Lookalikes of {domain} ==\n" + "\n".join(lines))

    report = "\n\n".join(sections)
    if not report:
        return "Nothing new."
    if not dry_run:
        send_email(f"Brand Intel: {len(sections)} alert(s)", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--dry-run", action="store_true", help="print the report instead of emailing it")
    print(run(parser.parse_args().dry_run))


if __name__ == "__main__":
    main()
