"""Match website locations to CRM accounts and turn the differences into proposals.

Every proposal is one reviewable decision on one account. Nothing here writes
to the CRM; apply.py does that, and only for proposals a reviewer approved.

Proposal kinds
  CREATE      location on the website, no CRM account yet
  REPARENT    account belongs under Bellhaven and the billing SOP allows moving it
  CHOW        account belongs under Bellhaven but has revenue AND open AR: keep it,
              create a new account under Bellhaven, link old -> new
  CHOW_LINK   Bellhaven account sold to another operator who already has an account
              for it: keep it (revenue + AR), link old -> the buyer's account
  RENAME      CRM name differs from the website name
  FIX_FIELDS  zip / city / state / care type / status differ from the website
  DUPLICATE   two or more accounts for the same facility: inactivate the extras
  STALE       account under Bellhaven that is no longer on the website
"""
import hashlib
import json

from . import config
from .normalize import (clean, digits, is_po_box, map_care, name_similarity, norm_name,
                        similarity, street_name, street_number)

KIND_ORDER = ["CHOW", "CHOW_LINK", "REPARENT", "CREATE", "DUPLICATE", "RENAME", "FIX_FIELDS", "STALE"]


# ---------------------------------------------------------------- helpers

def has_billing_hold(account):
    """The billing SOP: revenue history AND outstanding AR > 0 means do not re-parent."""
    return (account.get("lifetime_revenue") or 0) > 0 and (account.get("outstanding_ar") or 0) > 0


def is_retired(account):
    """Accounts already resolved by an earlier decision are left alone."""
    return bool(account.get("duplicate_of_account") or account.get("chow_current_account"))


def same_address(loc_like, account):
    """Same state, house number and street name, plus same zip or same city."""
    num = street_number(loc_like["street"])
    if not num or num != street_number(account.get("billing_street")):
        return False
    if (account.get("billing_state") or "") != loc_like["state"]:
        return False
    if similarity(street_name(loc_like["street"]), street_name(account.get("billing_street"))) \
            < config.STREET_NAME_MIN_SIMILARITY:
        return False
    return (account.get("billing_zip") or "") == loc_like["zip"] or \
        clean(account.get("billing_city")) == clean(loc_like["city"])


def account_as_location(a):
    return {"street": a.get("billing_street") or "", "city": a.get("billing_city") or "",
            "state": a.get("billing_state") or "", "zip": a.get("billing_zip") or ""}


def fmt_address(street, city, state, zip_code):
    return f"{street}, {city}, {state} {zip_code}".strip(", ")


def money(x):
    return f"${(x or 0):,.0f}"


def make_key(kind, account_id, slug, change):
    raw = json.dumps([kind, account_id, slug, change], sort_keys=True)
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


class Context:
    def __init__(self, accounts, contacts):
        self.by_id = {a["account_id"]: a for a in accounts}
        parent_ids = {a["parent_id"] for a in accounts if a.get("parent_id")}
        self.parents = {a["account_id"]: a for a in accounts
                        if a["account_id"] in parent_ids or a["name"].endswith("(Parent Account)")}
        bh = [a for a in self.parents.values() if a["name"] == config.BELLHAVEN_PARENT_NAME]
        if not bh:
            raise RuntimeError(f"Parent account '{config.BELLHAVEN_PARENT_NAME}' not found in CRM")
        self.bh = bh[0]
        self.facilities = [a for a in accounts if a["account_id"] not in self.parents]
        self.contacts = {}
        for c in contacts:
            self.contacts.setdefault(c["account_id"], []).append(c)

    def parent_name(self, account):
        p = self.parents.get(account.get("parent_id") or "")
        return p["name"].replace(" (Parent Account)", "") if p else "(no parent)"

    def snapshot(self, a):
        cs = self.contacts.get(a["account_id"], [])
        return {
            "account_id": a["account_id"],
            "name": a["name"],
            "parent": self.parent_name(a),
            "address": fmt_address(a.get("billing_street") or "", a.get("billing_city") or "",
                                   a.get("billing_state") or "", a.get("billing_zip") or ""),
            "care_type": a.get("care_type") or "",
            "status": a.get("status"),
            "phone": a.get("phone") or "",
            "lifetime_revenue": a.get("lifetime_revenue") or 0,
            "outstanding_ar": a.get("outstanding_ar") or 0,
            "contacts": [f"{c['name']} ({c.get('title') or 'no title'})" for c in cs],
        }


def website_snapshot(loc):
    return {
        "name": loc["name"],
        "address": fmt_address(loc["street"], loc["city"], loc["state"], loc["zip"]),
        "care_offerings": ", ".join(loc["care_offerings"]),
        "administrator": loc.get("administrator", ""),
        "phone": loc.get("phone", ""),
        "url": loc["url"],
        "listed_in_directory": loc.get("in_directory", True),
    }


def proposal(kind, title, account=None, loc=None, change=None, create=None, before=None,
             reason="", evidence=None, ctx=None):
    change = change or {}
    p = {
        "kind": kind,
        "title": title,
        "account_id": account["account_id"] if account else None,
        "location_slug": loc["slug"] if loc else None,
        "change": change,
        "create": create or {},
        "before": before if before is not None else ({k: account.get(k) for k in change} if account else {}),
        "evidence": {
            "reason": reason,
            "website": website_snapshot(loc) if loc else None,
            "crm": ctx.snapshot(account) if (account and ctx) else None,
            **(evidence or {}),
        },
    }
    p["key"] = make_key(kind, p["account_id"], p["location_slug"], {**change, **p["create"]})
    return p


# ---------------------------------------------------------------- matching

def survivor_score(a, loc, ctx):
    """Higher wins. Used to pick the account to keep when several match one location."""
    score = 0
    if a.get("parent_id") == ctx.bh["account_id"]:
        score += 100
    admin = clean(loc.get("administrator"))
    if admin and any(clean(c["name"]) == admin for c in ctx.contacts.get(a["account_id"], [])):
        score += 40
    if digits(loc.get("phone")) and digits(loc.get("phone")) == digits(a.get("phone")):
        score += 20
    if a.get("status") == "Active":
        score += 10
    if (a.get("lifetime_revenue") or 0) > 0:
        score += 5
    score += 10 * name_similarity(a["name"], loc["name"])
    return score


def find_candidates(loc, ctx):
    live = [a for a in ctx.facilities if not is_retired(a)]
    by_address = [a for a in live if same_address(loc, a)]
    if by_address:
        return by_address, "same street address"
    by_name = [a for a in live
               if (a.get("billing_state") or "") == loc["state"]
               and clean(a.get("billing_city")) == clean(loc["city"])
               and name_similarity(a["name"], loc["name"]) >= config.NAME_FALLBACK_MIN_SIMILARITY]
    if by_name:
        return by_name, "same name and city (street address differs)"
    return [], None


def near_misses(loc, ctx, exclude):
    out = []
    for a in ctx.facilities:
        if a["account_id"] in exclude:
            continue
        sim = name_similarity(a["name"], loc["name"])
        same_city = clean(a.get("billing_city")) == clean(loc["city"]) and a.get("billing_state") == loc["state"]
        if norm_name(a["name"]) == norm_name(loc["name"]) or (same_city and sim >= config.NEAR_MISS_MIN_SIMILARITY):
            out.append(f"{a['name']} ({ctx.parent_name(a)}) at "
                       f"{fmt_address(a.get('billing_street') or '', a.get('billing_city') or '', a.get('billing_state') or '', a.get('billing_zip') or '')}"
                       f" - not matched: different address")
    return out


def new_account_fields(loc, bh_id):
    care = map_care(loc["care_offerings"])
    return {
        "name": loc["name"],
        "parent_id": bh_id,
        "billing_street": loc["street"],
        "billing_city": loc["city"],
        "billing_state": loc["state"],
        "billing_zip": loc["zip"],
        "care_type": care[0] if care else "",
        "phone": loc.get("phone", ""),
        "status": "Active",
    }


def propose_for_location(loc, ctx):
    proposals = []
    candidates, basis = find_candidates(loc, ctx)
    bh_id = ctx.bh["account_id"]

    if not candidates:
        misses = near_misses(loc, ctx, exclude=set())
        proposals.append(proposal(
            "CREATE", f"Create '{loc['name']}' under Bellhaven", loc=loc,
            create=new_account_fields(loc, bh_id),
            reason="Listed on the Bellhaven website but no CRM account at this address or with this name in this city.",
            evidence={"near_misses": misses}, ctx=ctx))
        return proposals, {"slug": loc["slug"], "account_id": None, "basis": "no match"}

    ranked = sorted(candidates, key=lambda a: (-survivor_score(a, loc, ctx), a["account_id"]))
    keep, extras = ranked[0], ranked[1:]
    scores = {a["account_id"]: round(survivor_score(a, loc, ctx), 1) for a in ranked}

    for dup in extras:
        warn = []
        if has_billing_hold(dup):
            warn.append(f"Has revenue {money(dup.get('lifetime_revenue'))} and open AR {money(dup.get('outstanding_ar'))}: confirm with billing before inactivating.")
        if ctx.contacts.get(dup["account_id"]):
            warn.append(f"{len(ctx.contacts[dup['account_id']])} contact(s) stay on this inactive record; move them to the surviving account.")
        proposals.append(proposal(
            "DUPLICATE", f"Mark '{dup['name']}' (..{dup['account_id'][-6:]}) as duplicate of '{keep['name']}' "
                         f"(..{keep['account_id'][-6:]})", account=dup, loc=loc,
            change={"duplicate_of_account": keep["account_id"], "status": "Inactive"},
            reason=f"{len(ranked)} CRM accounts share this address ({basis}). Keeping '{keep['name']}' "
                   f"({keep['account_id']}) because it scores highest on: Bellhaven parent, administrator "
                   f"contact, phone, active status, billing history, name similarity.",
            evidence={"survivor": ctx.snapshot(keep), "survivor_scores": scores, "warnings": warn}, ctx=ctx))

    actions = []
    # Parent first: the billing SOP decides whether we can touch this account at all.
    if keep.get("parent_id") != bh_id:
        sop = {"lifetime_revenue": keep.get("lifetime_revenue") or 0,
               "outstanding_ar": keep.get("outstanding_ar") or 0,
               "billing_hold": has_billing_hold(keep)}
        if sop["billing_hold"]:
            proposals.append(proposal(
                "CHOW", f"Change of ownership: create new Bellhaven account for '{loc['name']}', keep the old one",
                account=keep, loc=loc, change={"chow_current_account": "<new account id>"},
                create=new_account_fields(loc, bh_id),
                before={"parent_id": keep.get("parent_id"), "chow_current_account": keep.get("chow_current_account")},
                reason=f"Website lists this community under Bellhaven but the CRM account sits under "
                       f"{ctx.parent_name(keep)}. It has revenue history ({money(sop['lifetime_revenue'])}) and "
                       f"open AR ({money(sop['outstanding_ar'])}), so per the billing SOP the old account is left "
                       f"exactly as it is: we create a new account under Bellhaven and set chow_current_account "
                       f"on the old one.",
                evidence={"sop": sop, "match_basis": basis}, ctx=ctx))
            return proposals, {"slug": loc["slug"], "account_id": keep["account_id"], "basis": basis,
                               "actions": ["CHOW"]}
        proposals.append(proposal(
            "REPARENT", f"Move '{keep['name']}' from {ctx.parent_name(keep)} to Bellhaven",
            account=keep, loc=loc, change={"parent_id": bh_id},
            reason=f"Website lists this community under Bellhaven. Billing SOP check: revenue "
                   f"{money(sop['lifetime_revenue'])}, open AR {money(sop['outstanding_ar'])}, so no billing "
                   f"hold and the account can be re-parented directly.",
            evidence={"sop": sop, "match_basis": basis}, ctx=ctx))
        actions.append("REPARENT")

    if keep["name"] != loc["name"]:
        cosmetic = norm_name(keep["name"]) == norm_name(loc["name"])
        proposals.append(proposal(
            "RENAME", f"Rename '{keep['name']}' to '{loc['name']}'", account=keep, loc=loc,
            change={"name": loc["name"]},
            reason=("Same name with different formatting; the website spelling is the operator's current name."
                    if cosmetic else "CRM name is outdated: the website lists this address under a new name."),
            evidence={"cosmetic_only": cosmetic, "match_basis": basis}, ctx=ctx))
        actions.append("RENAME")

    fixes, notes = {}, []
    if (keep.get("billing_zip") or "") != loc["zip"]:
        fixes["billing_zip"] = loc["zip"]
    if clean(keep.get("billing_city")) != clean(loc["city"]):
        fixes["billing_city"] = loc["city"]
    if (keep.get("billing_state") or "") != loc["state"]:
        fixes["billing_state"] = loc["state"]
    if basis != "same street address":
        fixes["billing_street"] = loc["street"]
        if is_po_box(keep.get("billing_street")):
            notes.append("The CRM street is a PO Box, which is often a deliberate billing address. "
                         "Check before replacing it with the physical address.")
    care = map_care(loc["care_offerings"])
    if care and keep.get("care_type") not in care:
        fixes["care_type"] = care[0]
    if keep.get("status") != "Active":
        fixes["status"] = "Active"
    if fixes:
        desc = ", ".join(f"{k} '{keep.get(k) or ''}' -> '{v}'" for k, v in fixes.items())
        proposals.append(proposal(
            "FIX_FIELDS", f"Update {', '.join(fixes)} on '{keep['name']}'", account=keep, loc=loc,
            change=fixes, reason=f"Website differs from CRM: {desc}.",
            evidence={"match_basis": basis, "warnings": notes}, ctx=ctx))
        actions.append("FIX_FIELDS")

    return proposals, {"slug": loc["slug"], "account_id": keep["account_id"], "basis": basis,
                       "actions": actions, "notes": notes, "duplicates": [d["account_id"] for d in extras]}


def propose_for_unlisted(ctx, matched_ids):
    """Accounts under the Bellhaven parent that the website no longer lists."""
    proposals = []
    bh_id = ctx.bh["account_id"]
    for a in ctx.facilities:
        if a.get("parent_id") != bh_id or a["account_id"] in matched_ids or is_retired(a):
            continue
        if a.get("status") == "Needs Review":
            continue
        here = account_as_location(a)
        buyers = [b for b in ctx.facilities
                  if b["account_id"] != a["account_id"] and not is_retired(b)
                  and b.get("parent_id") not in (None, "", bh_id) and same_address(here, b)]
        if buyers:
            buyer = buyers[0]
            sop = {"lifetime_revenue": a.get("lifetime_revenue") or 0,
                   "outstanding_ar": a.get("outstanding_ar") or 0, "billing_hold": has_billing_hold(a)}
            if sop["billing_hold"]:
                proposals.append(proposal(
                    "CHOW_LINK", f"Change of ownership: link '{a['name']}' to '{buyer['name']}' "
                                 f"({ctx.parent_name(buyer)})",
                    account=a, change={"chow_current_account": buyer["account_id"]},
                    reason=f"Not on the Bellhaven website, and {ctx.parent_name(buyer)} already has "
                           f"'{buyer['name']}' at the same address, so the facility changed owners. It has revenue "
                           f"({money(sop['lifetime_revenue'])}) and open AR ({money(sop['outstanding_ar'])}), so per "
                           f"the billing SOP this account is kept exactly as it is and only chow_current_account is "
                           f"set. The buyer's existing account is the 'new account', so no second one is created.",
                    evidence={"sop": sop, "new_owner_account": ctx.snapshot(buyer)}, ctx=ctx))
            else:
                proposals.append(proposal(
                    "DUPLICATE", f"Mark '{a['name']}' as duplicate of '{buyer['name']}' ({ctx.parent_name(buyer)})",
                    account=a, change={"duplicate_of_account": buyer["account_id"], "status": "Inactive"},
                    reason=f"Not on the Bellhaven website, and {ctx.parent_name(buyer)} already has an account at "
                           f"this address. No billing hold, so the old record is retired in favour of the buyer's.",
                    evidence={"sop": sop, "survivor": ctx.snapshot(buyer)}, ctx=ctx))
            continue
        proposals.append(proposal(
            "STALE", f"'{a['name']}' is no longer on the Bellhaven website: set Needs Review",
            account=a, change={"status": "Needs Review"},
            reason="Under the Bellhaven parent in the CRM but not listed anywhere on the website, and no other "
                   "operator has an account at this address. It may have closed or been sold; parent is left "
                   "unchanged until someone confirms which.",
            ctx=ctx))
    return proposals


def build_proposals(locations, accounts, contacts):
    ctx = Context(accounts, contacts)
    proposals, matches, matched_ids = [], [], set()
    for loc in locations:
        ps, m = propose_for_location(loc, ctx)
        proposals.extend(ps)
        # Both sides of the match, so the review app can show already-correct records too.
        m["website"] = website_snapshot(loc)
        if m.get("account_id") in ctx.by_id:
            m["crm"] = ctx.snapshot(ctx.by_id[m["account_id"]])
        matches.append(m)
        if m.get("account_id"):
            matched_ids.add(m["account_id"])
        matched_ids.update(m.get("duplicates", []))
    proposals.extend(propose_for_unlisted(ctx, matched_ids))
    proposals.sort(key=lambda p: (KIND_ORDER.index(p["kind"]), p["title"]))
    return proposals, matches, ctx
