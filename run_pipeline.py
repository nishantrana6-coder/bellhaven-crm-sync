"""Daily entry point.

    python run_pipeline.py          scrape + compare + queue new proposals (writes nothing to the CRM)
    python run_pipeline.py report   print a markdown summary of every proposal and decision
"""
import sys

from bellhaven_sync import config
from bellhaven_sync.crm import CRM
from bellhaven_sync.pipeline import run
from bellhaven_sync.store import Store


def report(store):
    rows = store.list()
    print("| # | Kind | Proposal | Status | Decided by | Note |")
    print("|---|---|---|---|---|---|")
    for p in rows:
        print(f"| {p['id']} | {p['kind']} | {p['title']} | {p['status']} | {p['decided_by'] or ''} | "
              f"{(p['decision_note'] or p['error'] or '').replace('|', '/')} |")


if __name__ == "__main__":
    store = Store(config.DB_PATH)
    if len(sys.argv) > 1 and sys.argv[1] == "report":
        report(store)
    else:
        run(store, CRM(config.API_URL, config.api_token()))
