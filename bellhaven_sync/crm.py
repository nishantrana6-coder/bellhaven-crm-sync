"""Thin client for the CRM sandbox API."""
import requests

# Fields we are allowed to send when creating or updating an account.
WRITABLE_FIELDS = {
    "name", "parent_id", "billing_street", "billing_city", "billing_state",
    "billing_zip", "care_type", "status", "phone", "note",
    "chow_current_account", "duplicate_of_account",
}


class CRMError(Exception):
    pass


class CRM:
    def __init__(self, api_url, token, session=None):
        self.api_url = api_url.rstrip("/")
        self.session = session or requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {token}"})

    def _request(self, method, path, **kwargs):
        resp = self.session.request(method, self.api_url + path, timeout=30, **kwargs)
        if resp.status_code >= 400:
            raise CRMError(f"{method} {path} -> {resp.status_code}: {resp.text[:500]}")
        return resp.json() if resp.content else {}

    @staticmethod
    def _unwrap(body):
        return body.get("data", body) if isinstance(body, dict) else body

    def _list(self, path, params=None):
        params = dict(params or {}, page=1, page_size=100)
        out = []
        while True:
            body = self._request("GET", path, params=params)
            rows = body.get("data", [])
            out.extend(rows)
            if not rows or len(out) >= body.get("total", len(out)):
                return out
            params["page"] += 1

    def list_accounts(self):
        return self._list("/accounts")

    def list_contacts(self):
        return self._list("/contacts")

    def get_account(self, account_id):
        return self._unwrap(self._request("GET", f"/accounts/{account_id}"))

    def update_account(self, account_id, fields):
        bad = set(fields) - WRITABLE_FIELDS
        if bad:
            raise CRMError(f"Refusing to write unknown fields: {sorted(bad)}")
        return self._unwrap(self._request("PATCH", f"/accounts/{account_id}", json=fields))

    def create_account(self, fields):
        bad = set(fields) - WRITABLE_FIELDS
        if bad:
            raise CRMError(f"Refusing to write unknown fields: {sorted(bad)}")
        created = self._unwrap(self._request("POST", "/accounts", json=fields))
        created["account_id"] = created.get("account_id") or created.get("id")
        if not created["account_id"]:
            # The account may exist now: check the CRM before approving again.
            raise CRMError(f"Create returned no account id; check the CRM before retrying: {created}")
        return created
