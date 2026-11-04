"""Write one approved proposal to the CRM.

Before writing, the live account is re-read and compared with what the
reviewer saw. If anything changed since the proposal was made, or the
billing SOP now points the other way, nothing is written and the proposal
is marked failed with the reason. After writing, the account is read back
to confirm the change landed.
"""
from datetime import date

from . import config
from .matcher import has_billing_hold


class ApplyError(Exception):
    pass


def _same(a, b):
    return (a or None) == (b or None)


def check_unchanged(live, before):
    drift = {k: (v, live.get(k)) for k, v in (before or {}).items() if not _same(live.get(k), v)}
    if drift:
        detail = "; ".join(f"{k}: proposed against '{v}', now '{now}'" for k, (v, now) in drift.items())
        raise ApplyError(f"CRM changed since this was proposed ({detail}). Re-run the pipeline.")


def with_note(existing, text, reviewer):
    line = f"[{date.today().isoformat()} bellhaven-sync, approved by {reviewer}] {text}"
    return f"{existing}\n{line}" if existing else line


def verify(crm, account_id, expected):
    live = crm.get_account(account_id)
    wrong = {k: (v, live.get(k)) for k, v in expected.items() if not _same(live.get(k), v)}
    if wrong:
        raise ApplyError(f"Write did not stick on {account_id}: {wrong}")
    return live


def apply_proposal(crm, p, reviewer, save_progress=None):
    """Returns a result dict. Raises ApplyError (nothing written) or CRMError."""
    kind, aid = p["kind"], p["account_id"]
    website_url = config.BASE_URL + p["evidence"]["website"]["url"] if p["evidence"].get("website") else ""

    if kind == "CREATE":
        result = dict(p.get("result") or {})
        if not result.get("created_account_id"):  # a retry must never create a second account
            fields = dict(p["create"])
            fields["note"] = with_note(None, f"Created from the Bellhaven website ({website_url}).", reviewer)
            result["created_account_id"] = crm.create_account(fields)["account_id"]
            if save_progress:
                save_progress(result)
        verify(crm, result["created_account_id"], {k: v for k, v in p["create"].items() if v})
        return result

    live = crm.get_account(aid)
    check_unchanged(live, p["before"])

    if kind == "REPARENT":
        if has_billing_hold(live):
            raise ApplyError("Billing SOP: this account now has revenue AND open AR, so it must not be "
                             "re-parented. Re-run the pipeline; it will propose a CHOW instead.")
        old_parent = (p["evidence"].get("crm") or {}).get("parent", "its previous parent")
        fields = {"parent_id": p["change"]["parent_id"],
                  "note": with_note(live.get("note"), f"Moved from {old_parent} to Bellhaven Senior Living: "
                                    f"listed on the Bellhaven website ({website_url}). Billing SOP checked: "
                                    f"revenue {live.get('lifetime_revenue') or 0}, open AR "
                                    f"{live.get('outstanding_ar') or 0}, no hold.", reviewer)}
        crm.update_account(aid, fields)
        verify(crm, aid, {"parent_id": fields["parent_id"]})
        return {"updated": ["parent_id", "note"]}

    if kind == "CHOW":
        if not has_billing_hold(live):
            raise ApplyError("Billing SOP no longer applies (revenue or AR is now zero). Re-run the "
                             "pipeline; it will propose a direct re-parent instead.")
        result = dict(p.get("result") or {})
        new_id = result.get("created_account_id")
        if not new_id:  # a retry after a partial failure must not create a second account
            fields = dict(p["create"])
            fields["note"] = with_note(None, f"Change of ownership successor of '{live['name']}' ({aid}), which "
                                       f"is kept unchanged for billing (revenue {live.get('lifetime_revenue')}, "
                                       f"open AR {live.get('outstanding_ar')}). Source: {website_url}", reviewer)
            new_id = crm.create_account(fields)["account_id"]
            result["created_account_id"] = new_id
            if save_progress:
                save_progress(result)
        # SOP: the old account is left exactly as it is apart from this one link.
        crm.update_account(aid, {"chow_current_account": new_id})
        verify(crm, aid, {"chow_current_account": new_id, "parent_id": live.get("parent_id")})
        result["updated"] = ["chow_current_account"]
        return result

    if kind == "CHOW_LINK":
        if not has_billing_hold(live):
            raise ApplyError("Billing SOP no longer applies (revenue or AR is now zero). Re-run the pipeline.")
        target = p["change"]["chow_current_account"]
        crm.update_account(aid, {"chow_current_account": target})
        verify(crm, aid, {"chow_current_account": target, "parent_id": live.get("parent_id")})
        return {"updated": ["chow_current_account"]}

    notes = {
        "RENAME": lambda: f"Renamed from '{live['name']}' to match the Bellhaven website ({website_url}).",
        "FIX_FIELDS": lambda: "Updated " + ", ".join(
            f"{k} '{live.get(k) or ''}' -> '{v}'" for k, v in p["change"].items()) + f" to match {website_url}.",
        "DUPLICATE": lambda: f"Duplicate of account {p['change']['duplicate_of_account']} (same facility); "
                             f"inactivated, survivor holds the facility going forward.",
        "STALE": lambda: f"Not listed on the Bellhaven website as of {date.today().isoformat()}. Confirm "
                         f"whether it closed or was sold before changing its parent.",
    }
    if kind not in notes:
        raise ApplyError(f"Unknown proposal kind {kind}")
    fields = dict(p["change"])
    fields["note"] = with_note(live.get("note"), notes[kind](), reviewer)
    crm.update_account(aid, fields)
    verify(crm, aid, p["change"])
    return {"updated": sorted(fields)}
