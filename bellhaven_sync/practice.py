"""Practice mode: a pretend copy of the CRM saved on this computer.

Start the app with BH_PRACTICE=1 (run_practice.bat). The review app then reads
and writes this copy instead of the real CRM. It starts from the snapshot in
fixtures/ (website and CRM as captured on 2026-11-02) and is saved to
data/practice_crm.json after every write. Nothing is sent to the real CRM.
"""
import copy
import html
import json
import os
import random
import string
import threading
from datetime import datetime, timezone

from . import config
from .crm import WRITABLE_FIELDS, CRMError
from .scraper import location_from_fields

FIXTURES = os.path.join(config.ROOT, "fixtures")

# Website care labels as shown on the site, longest first so they split cleanly.
LABELS = ["Short-Term Rehabilitation & Nursing", "Assisted Living", "Memory Support", "Independent Living"]


def _load(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return json.load(f)


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
    """The website as captured in the snapshot, in the same shape the scraper returns."""
    out = []
    for r in _load("bellhaven_locations.json"):
        fields = {k: html.unescape(v or "") for k, v in r["fields"].items()}
        linked = ["/"] if r["href"].endswith("meadows-of-findlay") else ["/communities"]
        out.append(location_from_fields(html.unescape(r["h1"]), fields,
                                        _split_offerings(r["fields"]["Care Offerings"]), r["href"], linked))
    return out


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


class PracticeCRM:
    """Same methods as bellhaven_sync.crm.CRM, backed by a local JSON file."""

    def __init__(self, path=None):
        self.path = path or os.path.join(os.path.dirname(config.DB_PATH), "practice_crm.json")
        self.lock = threading.Lock()
        if os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as f:
                state = json.load(f)
            self.accounts = {a["account_id"]: a for a in state["accounts"]}
            self.contacts = state["contacts"]
        else:
            self.reset()

    def reset(self):
        """Back to the snapshot, as if nothing had been approved."""
        with self.lock:
            self.accounts = {a["account_id"]: a for a in _load("crm_accounts.json")}
            self.contacts = _load("crm_contacts.json")
            self._save()

    def _save(self):
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"accounts": list(self.accounts.values()), "contacts": self.contacts}, f, indent=1)

    def _parent_name(self, pid):
        return self.accounts[pid]["name"] if pid in self.accounts else ""

    def list_accounts(self):
        return copy.deepcopy(list(self.accounts.values()))

    def list_contacts(self):
        return copy.deepcopy(self.contacts)

    def get_account(self, account_id):
        if account_id not in self.accounts:
            raise CRMError(f"GET /accounts/{account_id} -> 404 (practice CRM)")
        return copy.deepcopy(self.accounts[account_id])

    def update_account(self, account_id, fields):
        bad = set(fields) - WRITABLE_FIELDS
        if bad:
            raise CRMError(f"Refusing to write unknown fields: {sorted(bad)}")
        with self.lock:
            if account_id not in self.accounts:
                raise CRMError(f"PATCH /accounts/{account_id} -> 404 (practice CRM)")
            a = self.accounts[account_id]
            a.update(fields)
            a["parent_name"] = self._parent_name(a.get("parent_id"))
            a["updated_at"] = _now()
            self._save()
            return copy.deepcopy(a)

    def create_account(self, fields):
        bad = set(fields) - WRITABLE_FIELDS
        if bad:
            raise CRMError(f"Refusing to write unknown fields: {sorted(bad)}")
        with self.lock:
            new_id = "001PRAC" + "".join(random.choices(string.ascii_uppercase + string.digits, k=11))
            a = {"account_id": new_id, "name": "", "parent_id": "", "billing_street": "", "billing_city": "",
                 "billing_state": "", "billing_zip": "", "care_type": "", "status": "", "phone": "",
                 "lifetime_revenue": 0, "outstanding_ar": 0, "chow_current_account": "",
                 "duplicate_of_account": "", "note": "", "created_by_candidate": True, **fields}
            a["parent_name"] = self._parent_name(a.get("parent_id"))
            a["updated_at"] = _now()
            self.accounts[new_id] = a
            self._save()
            return copy.deepcopy(a)
