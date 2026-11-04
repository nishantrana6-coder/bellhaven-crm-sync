"""In-memory stand-in for the CRM API, same methods as bellhaven_sync.crm.CRM."""
import copy
import random
import string

from bellhaven_sync.crm import CRMError, WRITABLE_FIELDS


class FakeCRM:
    def __init__(self, accounts, contacts):
        self.accounts = {a["account_id"]: copy.deepcopy(a) for a in accounts}
        self.contacts = copy.deepcopy(contacts)
        self.writes = []

    def _parent_name(self, pid):
        return self.accounts[pid]["name"] if pid in self.accounts else ""

    def list_accounts(self):
        return [copy.deepcopy(a) for a in self.accounts.values()]

    def list_contacts(self):
        return copy.deepcopy(self.contacts)

    def get_account(self, account_id):
        if account_id not in self.accounts:
            raise CRMError(f"404 {account_id}")
        return copy.deepcopy(self.accounts[account_id])

    def update_account(self, account_id, fields):
        bad = set(fields) - WRITABLE_FIELDS
        if bad:
            raise CRMError(f"bad fields {bad}")
        a = self.accounts[account_id]
        a.update(fields)
        a["parent_name"] = self._parent_name(a.get("parent_id"))
        self.writes.append(("PATCH", account_id, dict(fields)))
        return copy.deepcopy(a)

    def create_account(self, fields):
        new_id = "001TEST" + "".join(random.choices(string.ascii_uppercase + string.digits, k=11))
        a = {"account_id": new_id, "lifetime_revenue": 0, "outstanding_ar": 0, "chow_current_account": None,
             "duplicate_of_account": None, "note": None, "created_by_candidate": True, **fields}
        a["parent_name"] = self._parent_name(a.get("parent_id"))
        self.accounts[new_id] = a
        self.writes.append(("POST", new_id, dict(fields)))
        return copy.deepcopy(a)
