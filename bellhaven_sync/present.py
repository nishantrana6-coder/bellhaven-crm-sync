"""Plain-language views of proposals and records for the review app.

Nothing here changes data: it turns stored proposals, matches and CRM records
into labels, side-by-side comparisons (website vs CRM) and readable dates.
"""
from datetime import datetime, timezone

from .normalize import digits, map_care, norm_name, norm_street

BH_NAME = "Bellhaven Senior Living"

KINDS = {
    "CHOW": {
        "tag": "Ownership change", "tone": "amber",
        "explain": "The website lists this community under Bellhaven, but billing still has money tied to the "
                   "old record. So the old record stays exactly as it is, a new Bellhaven record is created, "
                   "and the old one is linked to it."},
    "CHOW_LINK": {
        "tag": "Sold: link to new owner", "tone": "amber",
        "explain": "The website no longer lists this community, and another company already has a record at "
                   "the same address. Billing still has money tied to our record, so it stays as it is and is "
                   "linked to the new owner's record."},
    "REPARENT": {
        "tag": "Move to Bellhaven", "tone": "green",
        "explain": "The website lists this community under Bellhaven, but the CRM files it under another "
                   "company. There is no open balance, so the billing rule allows moving it directly."},
    "CREATE": {
        "tag": "New record", "tone": "blue",
        "explain": "This community is on the Bellhaven website but has no record in the CRM."},
    "DUPLICATE": {
        "tag": "Duplicate record", "tone": "amber",
        "explain": "The CRM has more than one record for this community. The best one is kept, and this extra "
                   "copy is marked Inactive and pointed at the record we keep. Nothing is deleted."},
    "RENAME": {
        "tag": "Name update", "tone": "green",
        "explain": "The CRM name doesn't match the name on the Bellhaven website."},
    "FIX_FIELDS": {
        "tag": "Details fix", "tone": "green",
        "explain": "Some details in the CRM don't match the Bellhaven website."},
    "STALE": {
        "tag": "Not on website", "tone": "grey",
        "explain": "This record sits under Bellhaven in the CRM, but the website no longer lists it. It is "
                   "flagged for someone to check, and its parent company is not changed."},
}

FIELD_LABELS = {
    "name": "Facility name", "parent_id": "Parent company", "billing_street": "Street",
    "billing_city": "City", "billing_state": "State", "billing_zip": "ZIP", "care_type": "Care type",
    "status": "Status", "phone": "Phone", "note": "Note",
    "chow_current_account": "Ownership link (chow_current_account)",
    "duplicate_of_account": "Duplicate of (duplicate_of_account)",
}

CHIPS = {"same": "Same", "format": "Same, written differently", "diff": "Different",
         "info": "Different (not updated by this tool)"}


# ------------------------------------------------------------------ basics
def when(ts):
    """'2026-11-03 23:42:00Z' -> 'Nov 3, 6:42 PM' in this computer's time zone."""
    if not ts:
        return ""
    try:
        dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%SZ").replace(tzinfo=timezone.utc).astimezone()
    except ValueError:
        return ts
    return dt.strftime("%b %d, %I:%M %p").replace(" 0", " ")


def money(x):
    return f"${(x or 0):,.0f}"


def short_id(account_id):
    return f"…{account_id[-6:]}" if account_id else ""


def split_address(addr):
    """'805 Colegate Dr, Marietta, OH 45750' -> street, city, state, zip."""
    parts = [p.strip() for p in (addr or "").split(",")]
    if len(parts) >= 3:
        st_zip = parts[-1].split()
        return {"street": ", ".join(parts[:-2]), "city": parts[-2],
                "state": st_zip[0] if st_zip else "", "zip": st_zip[1] if len(st_zip) > 1 else ""}
    return {"street": addr or "", "city": "", "state": "", "zip": ""}


def strip_parent(name):
    return (name or "").replace(" (Parent Account)", "")


def record_snapshot(a, parent_name="", contacts=()):
    """A live CRM account in the same shape as the snapshots stored in proposals."""
    return {
        "account_id": a.get("account_id"), "name": a.get("name") or "",
        "parent": strip_parent(parent_name or a.get("parent_name")) or "(no parent)",
        "address": ", ".join(x for x in [a.get("billing_street") or "", a.get("billing_city") or "",
                                         f"{a.get('billing_state') or ''} {a.get('billing_zip') or ''}".strip()] if x),
        "care_type": a.get("care_type") or "", "status": a.get("status") or "", "phone": a.get("phone") or "",
        "lifetime_revenue": a.get("lifetime_revenue") or 0, "outstanding_ar": a.get("outstanding_ar") or 0,
        "contacts": [f"{c['name']} ({c.get('title') or 'no title'})" for c in contacts],
    }


# ------------------------------------------------------------- comparison
def _exact_or(normalizer):
    def cmp(w, v):
        if (w or "").strip().lower() == (v or "").strip().lower():
            return "same"
        return "format" if normalizer(w) == normalizer(v) else "diff"
    return cmp


def _plain(w, v):
    return "same" if (w or "").strip().lower() == (v or "").strip().lower() else "diff"


def _care(w, v):
    mapped = map_care([x.strip() for x in (w or "").split(",")])
    if v in mapped:
        return "same" if (w or "").strip() == v else "format"
    return "diff"


def _phone(w, v):
    if (w or "") == (v or ""):
        return "same"
    return "format" if digits(w) == digits(v) else "info"


def _parent(w, v):
    return "same" if strip_parent(v) == w else "diff"


def _status(w, v):
    return "same" if v == "Active" else "diff"


def _contacts(w, v):
    return "same" if w and w.lower() in (v or "").lower() else "info"


def compare(website, records):
    """Rows of website value vs each CRM record's value, with a match status per CRM cell."""
    w = website or None
    wa = split_address(w["address"]) if w else {}
    specs = [
        ("Facility name", w and w["name"], lambda r: r["name"], _exact_or(norm_name)),
        ("Street", wa.get("street"), lambda r: split_address(r["address"])["street"], _exact_or(norm_street)),
        ("City", wa.get("city"), lambda r: split_address(r["address"])["city"], _plain),
        ("State", wa.get("state"), lambda r: split_address(r["address"])["state"], _plain),
        ("ZIP", wa.get("zip"), lambda r: split_address(r["address"])["zip"], _plain),
        ("Care", w and w["care_offerings"], lambda r: r["care_type"], _care),
        ("Parent company", w and BH_NAME, lambda r: r["parent"], _parent),
        ("Status", w and "Listed (open)", lambda r: r["status"], _status),
        ("Phone", w and w["phone"], lambda r: r["phone"], _phone),
        ("Administrator / contacts", w and w.get("administrator"),
         lambda r: ", ".join(r.get("contacts") or []) or "None in CRM", _contacts),
    ]
    rows = []
    for i, (label, wval, get, cmp) in enumerate(specs):
        cells = []
        for r in records:
            if not r:
                cells.append(("No matching record" if i == 0 else "", ""))
                continue
            v = get(r)
            cells.append((v, cmp(wval, v) if w else ""))
        website_cell = wval if w else ("Not listed on the website" if i == 0 else "")
        rows.append({"label": label, "website": website_cell, "cells": cells})
    return rows


def crm_only(records):
    """Facts only the CRM has, one cell per record."""
    def cells(fn):
        return [fn(r) if r else "" for r in records]
    return [
        {"label": "Revenue (all time)", "cells": cells(lambda r: money(r["lifetime_revenue"]))},
        {"label": "Unpaid balance (AR)", "cells": cells(lambda r: money(r["outstanding_ar"]))},
        {"label": "Record ID", "cells": cells(lambda r: r["account_id"])},
    ]


# ---------------------------------------------------------------- proposals
def show_value(field, value, p):
    ev = p["evidence"]
    if value in (None, ""):
        return "(empty)"
    if field == "parent_id":
        if value == (p["before"] or {}).get("parent_id"):
            return (ev.get("crm") or {}).get("parent") or value
        if value in ((p["create"] or {}).get("parent_id"), (p["change"] or {}).get("parent_id")):
            return BH_NAME
        return value
    if field == "duplicate_of_account":
        s = ev.get("survivor") or {}
        return f"{s['name']} (record {short_id(value)}), the record we keep" if s.get("account_id") == value else value
    if field == "chow_current_account":
        if value == "<new account id>":
            created = (p.get("result") or {}).get("created_account_id")
            return f"The new Bellhaven record{' (' + short_id(created) + ')' if created else ', created by this approval'}"
        o = ev.get("new_owner_account") or {}
        return f"{o['name']} ({o['parent']}), the new owner's record" if o.get("account_id") == value else value
    return str(value)


def name_and_place(p):
    ev = p["evidence"]
    w, c = ev.get("website") or {}, ev.get("crm") or {}
    name = w.get("name") or c.get("name") or p["title"]
    a = split_address(w.get("address") or c.get("address"))
    return name, ", ".join(x for x in [a["city"], a["state"]] if x)


def list_subtitle(p, place):
    ev, change = p["evidence"], p["change"]
    if p["status"] == "failed":
        return "Last attempt failed: open to retry"
    if p["kind"] == "DUPLICATE":
        return f"Extra copy, record {short_id(p['account_id'])} · {place}"
    if p["kind"] == "FIX_FIELDS":
        return "Fix " + ", ".join(FIELD_LABELS.get(k, k).lower() for k in change) + f" · {place}"
    if p["kind"] == "RENAME":
        return f"CRM says “{(ev.get('crm') or {}).get('name', '')}”"
    if p["kind"] == "CHOW_LINK":
        return f"Link to {(ev.get('new_owner_account') or {}).get('name', 'new owner')} · {place}"
    return place


def list_item(p):
    name, place = name_and_place(p)
    k = KINDS[p["kind"]]
    tag = {"applied": "Approved", "rejected": "Rejected", "superseded": "Withdrawn",
           "failed": "Failed"}.get(p["status"], k["tag"])
    tone = {"applied": "green", "rejected": "grey", "superseded": "grey", "failed": "red"}.get(p["status"], k["tone"])
    sub = list_subtitle(p, place)
    if p["status"] in ("applied", "rejected", "superseded"):
        sub = f"{k['tag']} · {when(p['decided_at'])}"
    return {"id": p["id"], "name": name, "sub": sub, "tag": tag, "tone": tone, "kind": p["kind"],
            "search": " ".join([name, place, p["title"]]).lower()}


def records_for(p, link):
    """CRM records a reviewer may want to open for this proposal: (label, record url, crm url)."""
    ev, out = p["evidence"], []
    if p["account_id"]:
        label = {"CHOW": "Old record (kept for billing)", "CHOW_LINK": "Our record",
                 "DUPLICATE": "Extra copy"}.get(p["kind"], "CRM record")
        out.append((label, p["account_id"]))
    created = (p.get("result") or {}).get("created_account_id")
    if created:
        out.append(("New Bellhaven record", created))
    if ev.get("survivor"):
        out.append(("Record we keep", ev["survivor"]["account_id"]))
    if ev.get("new_owner_account"):
        out.append(("New owner's record", ev["new_owner_account"]["account_id"]))
    return [{"label": label, "id": i, "url": f"/record/{i}?pid={p['id']}", "crm_url": link(i)} for label, i in out]


def change_sections(p):
    ev, kind, secs = p["evidence"], p["kind"], []
    if p["create"]:
        secs.append({"title": "Create a new record under Bellhaven Senior Living", "create": True,
                     "rows": [(FIELD_LABELS.get(k, k), "", show_value(k, v, p)) for k, v in p["create"].items()]})
    if p["change"]:
        name = (ev.get("crm") or {}).get("name") or "the record"
        title = {"CHOW": "On the old record, set only the ownership link",
                 "CHOW_LINK": "On our record, set only the ownership link"}.get(kind, f"Update “{name}”")
        secs.append({"title": title, "create": False,
                     "rows": [(FIELD_LABELS.get(k, k), show_value(k, (p["before"] or {}).get(k), p),
                               show_value(k, v, p)) for k, v in p["change"].items()]})
    note = {
        "CHOW": "Everything else on the old record (parent company, balances, name, status) stays exactly the "
                "same, and no note is added to it. The new record gets a note saying where it came from.",
        "CHOW_LINK": "Nothing else on our record changes and no note is added, so billing's record stays "
                     "exactly as it is.",
    }.get(kind, "A dated line saying what changed, why, and who approved it is added to the record's Note "
                "field. Existing note text is kept.")
    return secs, note


def banner(p):
    who, at, note = p.get("decided_by") or "", when(p.get("decided_at")), p.get("decision_note") or ""
    if p["status"] == "applied":
        return {"tone": "green", "title": "Approved and confirmed",
                "text": f"Approved by {who} on {at}. The change was written to the CRM, then read back to "
                        f"confirm it landed.", "note": note}
    if p["status"] == "rejected":
        return {"tone": "grey", "title": "Rejected",
                "text": f"Rejected by {who} on {at}. Nothing was changed in the CRM, and this suggestion "
                        f"won't come back on future runs.", "note": note}
    if p["status"] == "superseded":
        return {"tone": "grey", "title": "Withdrawn",
                "text": "The website or CRM changed after this was suggested, so it was withdrawn before "
                        "anyone approved it.", "note": ""}
    if p["status"] == "failed":
        return {"tone": "red", "title": "This didn't go through",
                "text": f"{p.get('error') or ''} Nothing was changed. You can approve again to retry.",
                "note": note}
    return None


def detail(p, link):
    ev, kind = p["evidence"], p["kind"]
    name, place = name_and_place(p)
    crm = ev.get("crm")
    if kind == "DUPLICATE":
        cols, recs = ["This extra copy", "Record we keep"], [crm, ev.get("survivor")]
    elif kind == "CHOW_LINK":
        cols, recs = ["Our record", "New owner's record"], [crm, ev.get("new_owner_account")]
    elif kind == "CHOW":
        cols, recs = ["Old record in our CRM"], [crm]
    else:
        cols, recs = ["Our CRM"], [crm]
    sections, change_note = change_sections(p)
    warnings = list(ev.get("warnings") or [])
    w = ev.get("website") or {}
    if w and not w.get("listed_in_directory", True):
        warnings.append("This community is linked only from the website's home page, not from the community "
                        "directory. A directory-only scraper would have missed it.")
    return {
        "id": p["id"], "status": p["status"], "kind": kind, "name": name, "place": place,
        "tag": KINDS[kind]["tag"], "tone": KINDS[kind]["tone"], "explain": KINDS[kind]["explain"],
        "reason": ev.get("reason") or "", "cosmetic": ev.get("cosmetic_only"), "check": bool(ev.get("warnings")),
        "cols": cols, "rows": compare(w or None, recs), "crm_only": crm_only(recs),
        "no_crm": crm is None and kind == "CREATE",
        "source_url": w.get("url"), "sop": ev.get("sop"), "warnings": warnings,
        "match_basis": ev.get("match_basis"), "near_misses": ev.get("near_misses") or [],
        "changes": sections, "change_note": change_note, "records": records_for(p, link), "banner": banner(p),
    }


def correct_item(m):
    w, c = m.get("website") or {}, m.get("crm") or {}
    name = w.get("name") or c.get("name") or m["slug"].replace("-", " ").title()
    a = split_address(w.get("address") or c.get("address"))
    place = ", ".join(x for x in [a["city"], a["state"]] if x)
    return {"id": m["slug"], "name": name, "sub": place or "Matched, nothing to change", "tag": "Already correct",
            "tone": "green", "kind": "", "search": f"{name} {place}".lower()}


def correct_detail(m, link):
    w, c = m.get("website"), m.get("crm")
    item = correct_item(m)
    return {
        "name": item["name"], "place": item["sub"], "basis": m.get("basis"), "notes": m.get("notes") or [],
        "has_snapshots": bool(w and c), "cols": ["Our CRM"], "rows": compare(w, [c]) if (w and c) else [],
        "crm_only": crm_only([c]) if c else [], "source_url": (w or {}).get("url"),
        "records": [{"label": "CRM record", "id": m["account_id"],
                     "url": f"/record/{m['account_id']}?slug={m['slug']}", "crm_url": link(m["account_id"])}],
    }
