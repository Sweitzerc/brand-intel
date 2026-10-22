import json
import re

from pacific_trade import cli, config, crosswalk, dashboard, demo


def _payload(html):
    m = re.search(r'<script id="data" type="application/json">(.*?)</script>', html, re.S)
    return json.loads(m.group(1))


def test_demo_dashboard_is_self_contained_and_flagged():
    tables = demo.demo_tables(seed=1)
    html = dashboard.render(tables, crosswalk.load_ports(), crosswalk.load_countries(), demo=True)
    assert "<script src" not in html and "<link" not in html          # no external dependencies
    data = _payload(html)
    assert data["meta"]["demo"] is True
    assert len(data["fact"]["port"]) == len(tables["fact_port_month_hs6_origin"])
    assert data["edges"]["v"] and data["global"]["hhi"] and data["ais"]["calls"]


def test_payload_escapes_script_close():
    tables = demo.demo_tables(seed=2)
    tables["fact_port_month_hs6_origin"].loc[0, "CTY_NAME"] = "</script><b>x"
    html = dashboard.render(tables, crosswalk.load_ports(), crosswalk.load_countries())
    assert "</script><b>x" not in html
    assert _payload(html)["fact"]["ctyName"][0] == "</script><b>x"


def test_demo_command_writes_dashboard():
    cli.main(["demo"])
    out = config.data_dir() / "out"
    assert "SYNTHETIC DEMO DATA" in (out / "dashboard.html").read_text()
    assert (out / "report.md").exists()
