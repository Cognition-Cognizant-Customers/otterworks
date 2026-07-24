"""System 3 — the external public web.

Crawls World Bank Open Data (no auth) for macro indicators that give the
internal enterprise drive some external market context. Stands in for the
"pull content off the public internet" leg of the workflow.
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa: E402

TIMEOUT = 20
UA = "OtterWorks-SinglePaneDemo/1.0 (+https://otterworks.app)"


def _fetch_indicator(base, country, indicator_id):
    url = (
        f"{base}/country/{country}/indicator/{indicator_id}"
        + "?"
        + urllib.parse.urlencode({"format": "json", "per_page": 5, "mrnev": 1})
    )
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        payload = json.load(resp)
    # World Bank returns [metadata, [observations...]]
    if not isinstance(payload, list) or len(payload) < 2 or not payload[1]:
        return None
    obs = payload[1][0]
    return {"value": obs.get("value"), "year": obs.get("date")}


def collect():
    base = config.WORLDBANK_BASE
    country = config.WORLDBANK_COUNTRY
    indicators = []
    for indicator_id, label, unit in config.WORLDBANK_INDICATORS:
        try:
            data = _fetch_indicator(base, country, indicator_id)
        except (urllib.error.URLError, ValueError, TimeoutError):
            data = None
        if data and data.get("value") is not None:
            indicators.append({
                "name": label,
                "value": round(float(data["value"]), 2),
                "year": data["year"],
                "unit": unit,
            })
    return {
        "source": "World Bank Open Data",
        "type": "External public web",
        "country": country,
        "source_url": (
            f"https://data.worldbank.org/country/{country}"
        ),
        "indicators": indicators,
    }


if __name__ == "__main__":
    print(json.dumps(collect(), indent=2))
