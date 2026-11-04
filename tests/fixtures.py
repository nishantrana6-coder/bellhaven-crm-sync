"""Load the offline snapshots in fixtures/ (captured from the sandbox on 2026-11-02)."""
import html
import json
import os

from bellhaven_sync.normalize import CARE_MAP
from bellhaven_sync.scraper import location_from_fields

HERE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fixtures")

# Website care labels as shown on the site, longest first so they split cleanly.
LABELS = ["Short-Term Rehabilitation & Nursing", "Assisted Living", "Memory Support", "Independent Living"]


def _split_offerings(text):
    text, out = html.unescape(text or ""), []
    while text.strip():
        text = text.strip()
        for label in LABELS:
            if text.startswith(label):
                out.append(label)
                text = text[len(label):]
                break
        else:
            raise ValueError(f"Unknown care label in {text!r}")
    return out


def load_locations():
    with open(os.path.join(HERE, "bellhaven_locations.json"), encoding="utf-8") as f:
        raw = json.load(f)
    out = []
    for r in raw:
        fields = {k: html.unescape(v or "") for k, v in r["fields"].items()}
        linked = ["/"] if r["href"].endswith("meadows-of-findlay") else ["/communities"]
        out.append(location_from_fields(html.unescape(r["h1"]), fields,
                                        _split_offerings(r["fields"]["Care Offerings"]), r["href"], linked))
    return out


def load_accounts():
    with open(os.path.join(HERE, "crm_accounts.json"), encoding="utf-8") as f:
        return json.load(f)


def load_contacts():
    with open(os.path.join(HERE, "crm_contacts.json"), encoding="utf-8") as f:
        return json.load(f)
