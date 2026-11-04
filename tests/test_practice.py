"""Practice mode: approvals land in the local copy only, and Start over resets everything."""
import importlib
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def test_practice_mode():
    os.environ["BH_PRACTICE"] = "1"
    os.environ["BH_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "practice.db")
    os.environ.pop("BH_API_TOKEN", None)
    import bellhaven_sync.config as cfg
    importlib.reload(cfg)
    import bellhaven_sync.practice as practice
    importlib.reload(practice)
    import app as review
    importlib.reload(review)

    def no_network(*a, **k):
        raise AssertionError("practice mode must not call the real CRM")
    review.CRM = no_network

    review.run_once(lambda m: None)
    client = review.app.test_client()
    page = client.get("/").get_data(as_text=True)
    assert "practice mode" in page and "/record/" in page
    pend = review.store.list(["pending"])
    assert len(pend) == 31

    marietta = [p for p in pend if p["kind"] == "CHOW" and "Marietta" in p["title"]][0]
    ashtabula = [p for p in pend if p["kind"] == "FIX_FIELDS" and "Ashtabula" in p["title"]][0]
    client.post(f"/p/{marietta['id']}/approve")
    client.post(f"/p/{ashtabula['id']}/reject", data={"note": "PO Box may be where invoices go"})
    old = review.crm().accounts[marietta["account_id"]]
    new_id = review.store.get(marietta["id"])["result"]["created_account_id"]
    assert old["chow_current_account"] == new_id and old["parent_name"] != "Bellhaven Senior Living (Parent Account)"
    acct_page = client.get(f"/practice-crm/{marietta['account_id']}").get_data(as_text=True)
    assert new_id in acct_page and "Its ownership link points here" not in acct_page and "chow_current_account" in acct_page
    assert "Bellhaven Senior Living" in client.get(f"/practice-crm/{new_id}").get_data(as_text=True)

    # Decision history, already-correct tab and the live record view.
    hist = client.get("/?view=history").get_data(as_text=True)
    assert "Approved and confirmed" in hist and "Bellhaven of Marietta" in hist and "Rejected" in hist
    correct = client.get("/?view=correct").get_data(as_text=True)
    assert "Already correct <span class=\"count\">12</span>" in correct and "CRM right now" not in correct
    rec = client.get(f"/record/{marietta['account_id']}?pid={marietta['id']}").get_data(as_text=True)
    assert "Bellhaven website" in rec and "CRM right now" in rec and "Cedar Trail" in rec and new_id[-6:] in rec

    # Saved to disk, so restarting the app keeps the practice CRM in step with the decisions.
    assert new_id in practice.PracticeCRM().accounts
    assert "0 new suggestions" in client.post("/run", follow_redirects=True).get_data(as_text=True)

    # Start over: clean CRM copy, fresh queue of 31.
    client.post("/practice/reset", follow_redirects=True)
    assert new_id not in review.crm().accounts
    assert not review.crm().accounts[marietta["account_id"]]["chow_current_account"]
    assert len(review.store.list(["pending"])) == 31 and not review.store.list(["applied", "rejected"])


if __name__ == "__main__":
    test_practice_mode(); print("ok practice")
