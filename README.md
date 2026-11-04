# Bellhaven CRM sync

A daily job that reads every Bellhaven community from the operator's website, compares it with the CRM, and queues each difference as a proposal. A reviewer approves or rejects each one in a small local app. Only approved proposals are written to the CRM, and the billing CHOW SOP is enforced at write time.

## Run it (Windows)

```
cd %USERPROFILE%\Documents\bellhaven-assessment
setup.bat            one time: creates .venv and installs requests, beautifulsoup4, flask
run_pipeline.bat     scrape + compare + queue proposals (reads only, never writes to the CRM)
run_app.bat          review app at http://127.0.0.1:5000
run_practice.bat     practice mode at http://127.0.0.1:5001: same app on a local copy of the CRM
```

Practice mode (`BH_PRACTICE=1`) runs the same pipeline and review app against a copy of the sandbox saved in `data/practice_crm.json`, so the review flow can be rehearsed without touching the real CRM. Its CRM links open a local page for each account, and "Start over" resets the copy and the queue.

The API token is read from `BH_API_TOKEN` or from a `.bh_token` file in this folder (gitignored). Offline tests run on a snapshot of the sandbox: `python tests\test_end_to_end.py` and `python tests\test_scraper_and_app.py`.

## How it works

| Step | File | What it does |
|---|---|---|
| Scrape | `bellhaven_sync/scraper.py` | Crawls every internal page and collects every `/communities/<slug>` link, then parses name, street, city, state, zip, care offerings, administrator and phone from each detail page. |
| Read CRM | `bellhaven_sync/crm.py` | Pages through all accounts and contacts. Writes are restricted to a whitelist of fields. |
| Match | `bellhaven_sync/matcher.py` | Matches each location to accounts by address, falls back to name within the same city, and classifies the result. |
| Review | `app.py` | Shows each proposal with website and CRM side by side, the match basis, the SOP check, and warnings. Approve writes; Reject records the decision. |
| Apply | `bellhaven_sync/apply.py` | Re-reads the live account, refuses if it changed since the proposal or if the SOP now points the other way, writes, then reads back to confirm. |
| State | `bellhaven_sync/store.py` | SQLite log of runs, proposals and decisions. |

**Why crawl instead of reading the directory.** The directory lists 34 communities, but the home page says 35 and links "Bellhaven Meadows of Findlay" as new this year. A directory-only scraper misses the newest acquisition, which is exactly the record most likely to be wrong in the CRM. Each location records where it was linked from, and the app flags anything found outside the directory.

**Matching.** Addresses are normalized: suffixes and directions are reduced to one form (Avenue/Ave, Northwest/NW, Pike/Pk) and the house number is compared separately. A location matches an account when the following hold:

- same state;
- same house number;
- street name at least 85% similar;
- same zip, or same city when the zip is mistyped.

If nothing matches by address, it falls back to the same city with a facility name at least 85% similar after normalizing Rehab/Rehabilitation, Centre/Center, &/and, and "the/of/at". That fallback is what catches the Ashtabula account, whose street is a PO Box. Matching runs against all accounts, not a "bellhaven" name search, because several Bellhaven communities sit in the CRM under other names or other parents. Similar-but-unmatched accounts are shown as "similar, not matched" so a reviewer can see what was ruled out:

- Amberly Manor in Colorado Springs, CO is a different facility from Amberly Manor in Hudson, OH.
- Union Square Senior Living is at a different street in New Albany.

**Classification.** Each location ends up as one of the following:

- **Confident match.** One account at the address, already correct, so nothing is queued.
- **Match that needs a fix.** One or more of:
  - `REPARENT`;
  - `CHOW`;
  - `RENAME`, flagged when the difference is formatting only;
  - `FIX_FIELDS` for zip, city, state, street, care type or status.
- **Several accounts at one address.** The best one survives and the rest get `DUPLICATE`. The survivor is chosen by, in order:
  1. already under Bellhaven;
  2. a CRM contact whose name matches the administrator listed on the website;
  3. same phone;
  4. active;
  5. has billing history;
  6. name similarity.

  The scores are shown in the app.
- **No account.** `CREATE` under Bellhaven.

Accounts under the Bellhaven parent that the website does not list get one of two outcomes:

- **Another operator has an account at the same address:** the facility was sold, so `CHOW_LINK` is queued (or `DUPLICATE` if there is no billing hold).
- **No other account there:** `STALE` sets the status to Needs Review. Their parent is not changed until someone confirms whether they closed or were sold.

**Billing SOP.** Moving an account to a different parent checks `lifetime_revenue` and `outstanding_ar`:

- **Revenue history and AR > 0:** the old account is left exactly as it is. Only `chow_current_account` is set, pointing to a new account created under the correct parent. No note, status or name change is made on the old account; the explanation goes on the new one.
- **Otherwise:** the account is re-parented directly. Lima ($47K revenue, $0 AR) and Findlay ($22K, $0 AR) take this path.

The check runs twice: when the proposal is built, and again on live data at the moment of approval. If AR changed in between, the write is refused and the next run proposes the other path. A CHOW retry never creates a second account, because the created id is saved before the link is written.

**Duplicates, status and notes.** There is no merge or delete, so a losing duplicate gets `duplicate_of_account` set to the survivor and status `Inactive`. Every write except the CHOW old account appends a dated line to `note` with what changed, why, the source URL and who approved it. Existing note text is kept.

**Re-runs are safe.** Each proposal has a key built from kind, account, location and the proposed values. A key is stored once, ever:

- Pending, approved and rejected items are never proposed again.
- Once a change is applied, the data matches and nothing is generated.
- Accounts already resolved (a duplicate or CHOW link is set) are excluded from matching.
- If a pending item is no longer produced, because someone fixed it by hand or the website changed, it is marked superseded so nobody approves stale data.

**Schedule.** `schedule/crontab.txt` (daily 06:15) and `schedule/windows_task.txt`. I chose cron on the host that runs the review app rather than GitHub Actions, because the decision log has to persist between runs and be shared with the app. Actions would need that state moved to a hosted database.

## What it found in my sandbox

35 website locations (34 in the directory, plus Findlay from the home page) and 121 CRM accounts produced 31 proposals:

| Kind | Count | Cases |
|---|---|---|
| CHOW | 2 | Marietta, Tiffin: under Cedar Trail with revenue and open AR, so new Bellhaven accounts are created and linked |
| CHOW_LINK | 1 | Bellhaven of Sandusky: not on the website; Millstone Health Partners has an account at the same address ($130K revenue, $5.2K AR), so it is linked to Millstone's account instead of creating a second one |
| REPARENT | 4 | Lima (Harborview), Findlay (no parent), Zanesville (Cedar Trail), Kettering survivor (Harborview) |
| CREATE | 4 | Batavia, Carlisle PA (not New Carlisle OH), Bellhaven at Union Square, Amberly Manor (Hudson, OH) |
| DUPLICATE | 7 | Owosso ×1, Port Clinton ×1, Erie ×1, Monroe ×2, Kettering ×2. These are mostly old Harborview and Cedar Trail records for communities that joined Bellhaven. |
| RENAME | 9 | 5 outdated names (Chesterton, Willow Creek, Chagrin Falls, Zanesville, Kettering) and 4 formatting-only (Grove City, Sycamore Ridge, Arbors at Bellhaven - Dayton, Ashland) |
| FIX_FIELDS | 2 | Portsmouth zip typo 45626 to 45662; Ashtabula street is a PO Box |
| STALE | 2 | Coldwater, Alliance: under Bellhaven but not on the website, no other owner found, set to Needs Review |

12 locations matched cleanly with nothing to change.

Judgment calls worth flagging:

- **Kettering.** Three accounts share the address, none under Bellhaven and none with contacts, matching phone or revenue. The survivor (Kettering Care Centre, Harborview) won on name similarity by a hair. Any of the three would be defensible; what matters is that exactly one survives and the other two point at it.
- **Ashtabula.** The PO Box is proposed with a warning rather than suppressed, so a person decides whether billing mail should keep going to the PO Box. [Write your decision and reason here.]
- **Owosso.** The losing duplicate still has an active contact (Admissions Director). The app warns about it; moving contacts is listed under next steps below.

## Limitations and next steps

- Move contacts from a losing duplicate to the survivor (the contacts API supports PATCH). I left it out so every write stays on accounts, which the review covers.
- Let the reviewer choose a different survivor in the app instead of only approving or rejecting the computed one.
- Multi-service communities (Erie, Findlay) list two care offerings, but `care_type` holds one. I keep the existing value when it is one of the offerings and use the first offering for new accounts.
- No LLM is needed: the rules are deterministic and every decision shows its evidence. An LLM would fit as a second opinion on name-only fallbacks.
- Alerting: post the run summary to Slack and alert if the scrape returns far fewer locations than the last run, which would mean a broken scraper rather than closed communities.

## How I used AI

[Write 3 to 4 honest sentences in your own words: what you asked the AI to do, what you checked yourself, and what you changed after checking.]

## Time spent

[Fill in your actual time.]
