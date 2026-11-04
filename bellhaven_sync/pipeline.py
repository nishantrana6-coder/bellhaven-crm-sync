"""One pipeline run: scrape the website, read the CRM, store new proposals."""
from . import config
from .matcher import build_proposals
from .scraper import scrape_locations


def run(store, crm, locations=None, log=print):
    run_id = store.start_run()
    if locations is None:
        locations = scrape_locations(config.BASE_URL)
    accounts = crm.list_accounts()
    contacts = crm.list_contacts()
    log(f"Scraped {len(locations)} website locations "
        f"({sum(1 for l in locations if not l['in_directory'])} not in the directory); "
        f"read {len(accounts)} CRM accounts and {len(contacts)} contacts.")

    proposals, matches, _ = build_proposals(locations, accounts, contacts)
    new, superseded = store.add_proposals(run_id, proposals)
    store.finish_run(run_id, n_locations=len(locations), n_accounts=len(accounts),
                     n_generated=len(proposals), n_new=new, n_superseded=superseded, matches=matches)
    log(f"{len(proposals)} differences found; {new} new proposals queued for review, "
        f"{len(proposals) - new} already decided or already pending, {superseded} superseded.")
    return run_id
