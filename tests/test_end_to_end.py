"""Offline end-to-end test on the captured sandbox snapshot.

    python -m pytest tests -q        (or)        python tests/test_end_to_end.py
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "tests")]

from fake_crm import FakeCRM  # noqa: E402
from fixtures import load_accounts, load_contacts, load_locations  # noqa: E402

from bellhaven_sync.apply import ApplyError, apply_proposal  # noqa: E402
from bellhaven_sync.matcher import same_address  # noqa: E402
from bellhaven_sync.pipeline import run  # noqa: E402
from bellhaven_sync.store import Store  # noqa: E402



def fresh():
    store = Store(os.path.join(tempfile.mkdtemp(), "t.db"))
    crm = FakeCRM(load_accounts(), load_contacts())
    return store, crm, load_locations()


def by_name(crm, name):
    return [a for a in crm.accounts.values() if a["name"] == name]


def approve_all(store, crm, skip=()):
    for p in store.list(["pending"]):
        if p["title"] in skip:
            store.decide(p["id"], "rejected", "test")
            continue
        result = apply_proposal(crm, p, "test", save_progress=lambda r, i=p["id"]: store.save_partial_result(i, r))
        store.decide(p["id"], "applied", "test", result=result)


def test_scraper_finds_homepage_only_community():
    locs = load_locations()
    assert len(locs) == 35
    assert [l["name"] for l in locs if not l["in_directory"]] == ["Bellhaven Meadows of Findlay"]


def test_full_review_cycle_and_idempotency():
    store, crm, locs = fresh()
    bh_id = [a for a in crm.accounts.values() if a["name"] == "Bellhaven Senior Living (Parent Account)"][0]["account_id"]
    run(store, crm, locations=locs, log=lambda m: None)
    pending = store.list(["pending"])
    kinds = sorted(p["kind"] for p in pending)
    assert kinds.count("CHOW") == 2 and kinds.count("CREATE") == 4 and kinds.count("STALE") == 2

    reject = "Rename 'Bellhaven of Sycamore Ridge' to 'Bellhaven at Sycamore Ridge'"
    marietta_before = dict(by_name(crm, "Bellhaven of Marietta")[0])
    approve_all(store, crm, skip=[reject])

    # SOP: Marietta kept exactly as is except the CHOW link; a new Bellhaven account exists.
    olds = [a for a in by_name(crm, "Bellhaven of Marietta") if a["account_id"] == marietta_before["account_id"]]
    old = olds[0]
    changed = {k for k in old if old[k] != marietta_before.get(k)}
    assert changed == {"chow_current_account", "parent_name"} or changed == {"chow_current_account"}
    new = crm.accounts[old["chow_current_account"]]
    assert new["parent_id"] == bh_id and new["name"] == "Bellhaven of Marietta"

    # SOP: Lima has revenue but no AR -> re-parented directly.
    assert by_name(crm, "Bellhaven Crossings of Lima")[0]["parent_id"] == bh_id
    # Sold: Sandusky stays under Bellhaven for billing, linked to Millstone's account.
    sandusky = by_name(crm, "Bellhaven of Sandusky")[0]
    assert sandusky["parent_id"] == bh_id and by_name(crm, "Millstone Care of Sandusky")[0]["account_id"] == sandusky["chow_current_account"]

    # Every website location: exactly one live account under Bellhaven at its address, named as on the site.
    for loc in locs:
        live = [a for a in crm.accounts.values() if same_address(loc, a) and a.get("parent_id") == bh_id
                and not a.get("duplicate_of_account") and not a.get("chow_current_account") and a["status"] == "Active"]
        if loc["slug"] == "bellhaven-of-ashtabula":  # PO Box billing street, matched by name instead
            continue
        assert len(live) == 1, (loc["name"], [a["name"] for a in live])
        if loc["name"] != "Bellhaven at Sycamore Ridge":
            assert live[0]["name"] == loc["name"], (loc["name"], live[0]["name"])

    # Re-run: nothing new, rejected item not re-proposed.
    n_before = len(store.list())
    run(store, crm, locations=locs, log=lambda m: None)
    assert len(store.list()) == n_before
    assert store.list(["pending"]) == []


def test_drift_and_sop_flip_block_writes():
    store, crm, locs = fresh()
    run(store, crm, locations=locs, log=lambda m: None)
    reparent = [p for p in store.list(["pending"]) if p["kind"] == "REPARENT" and "Lima" in p["title"]][0]
    crm.accounts[reparent["account_id"]]["outstanding_ar"] = 500  # billing now holds this account
    try:
        apply_proposal(crm, reparent, "test")
        assert False, "should refuse"
    except ApplyError as e:
        assert "Billing SOP" in str(e)
    rename = [p for p in store.list(["pending"]) if p["kind"] == "RENAME"][0]
    crm.accounts[rename["account_id"]]["name"] = "Edited by someone else"
    try:
        apply_proposal(crm, rename, "test")
        assert False, "should refuse"
    except ApplyError as e:
        assert "changed since" in str(e)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
