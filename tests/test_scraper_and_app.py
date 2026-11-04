"""Scraper parsing on site-shaped HTML, and the review app's approve/reject flow."""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "tests")]

from fake_crm import FakeCRM  # noqa: E402
from fixtures import load_accounts, load_contacts, load_locations  # noqa: E402

from bellhaven_sync.scraper import scrape_locations  # noqa: E402

NAV = '<header><nav><a href="/">Home</a><a href="/communities">Our Communities</a><a href="/about">About Us</a></nav></header>'
PAGES = {
    "/": NAV + '<p>New this year: <a href="/communities/bellhaven-meadows-of-findlay">Findlay</a></p>',
    "/about": NAV + "<p>About</p>",
    "/communities": NAV + '<div class="cardgrid"><div class="card"><h3><a href="/communities/bellhaven-of-maplewood">Bellhaven of Maplewood</a></h3>'
                          '<div class="city">Maplewood, OH</div></div></div><a href="/communities?page=2">Next</a>',
    "/communities?page=2": NAV + '<div class="card"><h3><a href="/communities/bellhaven-shores-of-erie">Bellhaven Shores of Erie</a></h3></div>'
                                 '<a href="/communities?page=1">Prev</a>',
    "/communities?page=1": "redirect-ish duplicate of page 1",
    "/communities/bellhaven-of-maplewood": NAV + '<div class="wrap"><h1>Bellhaven of Maplewood</h1><dl class="detail">'
        '<dt>Address</dt><dd>210 Orchard Lane<br>Maplewood, OH 44280</dd><dt>Care Offerings</dt><dd><span class="badge">Assisted Living</span></dd>'
        '<dt>Administrator</dt><dd>Nadia Duval</dd><dt>Phone</dt><dd>(614) 250-9447</dd></dl></div>',
    "/communities/bellhaven-shores-of-erie": NAV + '<div class="wrap"><h1>Bellhaven Shores of Erie</h1><dl class="detail">'
        '<dt>Address</dt><dd>4930 W Lake Rd<br>Erie, PA 16505</dd><dt>Care Offerings</dt><dd><span class="badge">Assisted Living</span>'
        '<span class="badge">Memory Support</span></dd><dt>Administrator</dt><dd>Angela Quinn</dd><dt>Phone</dt><dd>(614) 792-9343</dd></dl></div>',
    "/communities/bellhaven-meadows-of-findlay": NAV + '<div class="wrap"><h1>Bellhaven Meadows of Findlay</h1><dl class="detail">'
        '<dt>Address</dt><dd>1800 N Blanchard St<br>Findlay, OH 45840</dd><dt>Care Offerings</dt><dd><span class="badge">Short-Term Rehabilitation &amp; Nursing</span></dd>'
        '<dt>Administrator</dt><dd>Sam Pruitt</dd><dt>Phone</dt><dd>(231) 533-2969</dd></dl></div>',
}


class Resp:
    def __init__(self, text, status=200):
        self.text, self.status_code, self.headers = text, status, {"content-type": "text/html; charset=utf-8"}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class FakeSession:
    def get(self, url, timeout=None):
        path = url.replace("https://site", "") or "/"
        return Resp(PAGES[path]) if path in PAGES else Resp("nf", 404)


def test_scraper_crawls_directory_and_homepage():
    locs = {l["slug"]: l for l in scrape_locations("https://site", session=FakeSession())}
    assert set(locs) == {"bellhaven-of-maplewood", "bellhaven-shores-of-erie", "bellhaven-meadows-of-findlay"}
    erie = locs["bellhaven-shores-of-erie"]
    assert (erie["street"], erie["city"], erie["state"], erie["zip"]) == ("4930 W Lake Rd", "Erie", "PA", "16505")
    assert erie["care_offerings"] == ["Assisted Living", "Memory Support"] and erie["in_directory"]
    findlay = locs["bellhaven-meadows-of-findlay"]
    assert not findlay["in_directory"] and findlay["care_offerings"] == ["Short-Term Rehabilitation & Nursing"]


def test_review_app_approve_and_reject():
    os.environ["BH_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "app.db")
    os.environ["BH_API_TOKEN"] = "test-token"
    import importlib
    import bellhaven_sync.config as cfg
    importlib.reload(cfg)
    import app as review
    importlib.reload(review)
    fake = FakeCRM(load_accounts(), load_contacts())
    review.crm = lambda: fake
    from bellhaven_sync.pipeline import run
    review.run_pipeline = lambda store, crm, log: run(store, crm, locations=load_locations(), log=log)
    run(review.store, fake, locations=load_locations(), log=lambda m: None)
    client = review.app.test_client()
    page = client.get("/").get_data(as_text=True)
    assert "Approve &amp; apply" in page and "Billing rule" in page and "Bellhaven website" in page

    pend = review.store.list(["pending"])
    zip_fix = [p for p in pend if p["kind"] == "FIX_FIELDS" and "Portsmouth" in p["title"]][0]
    cosmetic = [p for p in pend if "Sycamore" in p["title"]][0]
    client.post(f"/p/{zip_fix['id']}/approve", data={"note": "zip typo"})
    client.post(f"/p/{cosmetic['id']}/reject", data={"note": "cosmetic"})
    assert review.store.get(zip_fix["id"])["status"] == "applied"
    assert fake.accounts[zip_fix["account_id"]]["billing_zip"] == "45662"
    assert review.store.get(cosmetic["id"])["status"] == "rejected"
    assert fake.accounts[cosmetic["account_id"]]["name"] == "Bellhaven of Sycamore Ridge"
    # Re-run from the app: nothing new appears.
    flash_page = client.post("/run", follow_redirects=True).get_data(as_text=True)
    assert "0 new suggestions" in flash_page
    assert len(review.store.list(["pending"])) == len(pend) - 2


if __name__ == "__main__":
    test_scraper_crawls_directory_and_homepage(); print("ok scraper")
    test_review_app_approve_and_reject(); print("ok app")
