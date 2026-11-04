"""Local review app.  Run:  python app.py   then open http://127.0.0.1:5000

Left: the list of suggested changes. Right: one suggestion with what the
Bellhaven website says next to what the CRM says, what approving will do,
and Approve / Reject. Approve writes to the CRM through the API; Reject
records the decision so it is never suggested again. Nothing reaches the
CRM without an Approve click. "Decision history" lists every decision, and
"View current record" reads a record live and shows it beside the website.

Practice mode (BH_PRACTICE=1, run_practice.bat) runs the same app against a
copy of the CRM saved on this computer.
"""
import getpass
import os
from urllib.parse import urlencode

from flask import Flask, abort, flash, redirect, render_template, request, url_for
from markupsafe import escape

from bellhaven_sync import config, present
from bellhaven_sync.apply import ApplyError, apply_proposal
from bellhaven_sync.crm import CRM, CRMError
from bellhaven_sync.matcher import KIND_ORDER
from bellhaven_sync.pipeline import run as run_pipeline
from bellhaven_sync.store import Store

app = Flask(__name__)
app.secret_key = "local-review-app"
store = Store(config.DB_PATH)
REVIEWER = os.environ.get("BH_REVIEWER") or getpass.getuser()

_practice_crm = None


def crm():
    if config.PRACTICE:  # a pretend copy saved on this computer; the real CRM is never called
        global _practice_crm
        if _practice_crm is None:
            from bellhaven_sync.practice import PracticeCRM
            _practice_crm = PracticeCRM()
        return _practice_crm
    return CRM(config.API_URL, config.api_token())


def crm_link(account_id):
    """Where 'Open in CRM' goes: the sandbox's CRM browser, or the practice copy's page."""
    if not account_id:
        return ""
    if config.PRACTICE:
        return f"/practice-crm/{account_id}"
    return f"{config.BASE_URL}/crm/{config.api_token()}/accounts/{account_id}"


def run_once(log):
    if config.PRACTICE:
        from bellhaven_sync.practice import load_locations
        return run_pipeline(store, crm(), locations=load_locations(), log=log)
    return run_pipeline(store, crm(), log=log)


def run_summary():
    r = store.last_run() or {}
    msg = (f"Checked {r.get('n_locations', 0)} website communities against {r.get('n_accounts', 0)} CRM records: "
           f"{r.get('n_new', 0)} new suggestions. Anything you already approved or rejected was not suggested again.")
    if r.get("n_superseded"):
        msg += f" {r['n_superseded']} older suggestion(s) were withdrawn because the data changed."
    return msg


def page_link(view, item_id=None, q="", kind=""):
    params = {"view": view}
    if item_id is not None:
        params["id"] = item_id
    if q:
        params["q"] = q
    if kind:
        params["kind"] = kind
    return "/?" + urlencode(params)


# ------------------------------------------------------------------ pages
@app.get("/")
def index():
    view = request.args.get("view", "queue")
    q = request.args.get("q", "").strip()
    kind = request.args.get("kind", "")

    pending = store.list(["pending", "failed"])
    pending.sort(key=lambda p: (KIND_ORDER.index(p["kind"]), p["title"]))
    history = sorted(store.list(["applied", "rejected", "superseded"]),
                     key=lambda p: p["decided_at"] or "", reverse=True)
    last = store.last_run()
    correct = [m for m in (last or {}).get("matches", [])
               if m.get("account_id") and not m.get("actions") and not m.get("duplicates")]

    if view == "correct":
        items = [present.correct_item(m) for m in correct]
    else:
        source = history if view == "history" else pending
        items = [present.list_item(p) for p in source]
    if q:
        items = [i for i in items if q.lower() in i["search"]]
    if kind and view != "correct":
        items = [i for i in items if i["kind"] == kind]

    wanted = request.args.get("id")
    sel = next((i for i in items if str(i["id"]) == wanted), items[0] if items else None)
    d = None
    if sel and view == "correct":
        d = present.correct_detail(next(m for m in correct if m["slug"] == sel["id"]), crm_link)
    elif sel:
        d = present.detail(store.get(sel["id"]), crm_link)

    counts = store.counts()
    stats = {"pending": counts.get("pending", 0) + counts.get("failed", 0), "applied": counts.get("applied", 0),
             "rejected": counts.get("rejected", 0), "failed": counts.get("failed", 0)}
    tabs = [("queue", "To review", stats["pending"]), ("history", "Decision history", len(history)),
            ("correct", "Already correct", len(correct))]
    kinds = [(k, present.KINDS[k]["tag"]) for k in KIND_ORDER]
    crm_home = None if config.PRACTICE else f"{config.BASE_URL}/crm/{config.api_token()}/accounts"
    return render_template("review.html", view=view, q=q, kind=kind, items=items, sel=sel, d=d, stats=stats,
                           tabs=tabs, kinds=kinds, last=last, when=present.when, link=page_link,
                           practice=config.PRACTICE, crm_home=crm_home, chips=present.CHIPS, config_base=config.BASE_URL,
                           money=present.money, reviewer=REVIEWER)


@app.get("/record/<account_id>")
def record(account_id):
    """The record as it is in the CRM right now, next to what the website says. Read-only."""
    c = crm()
    try:
        a = c.get_account(account_id)
    except Exception as e:  # show the problem inside the dialog
        return f'<p class="box red">Couldn\'t read record {escape(account_id)}: {escape(str(e))}</p>'

    def name_of(i):
        if not i:
            return ""
        try:
            return c.get_account(i).get("name") or i
        except Exception:
            return i

    parent = a.get("parent_name") or name_of(a.get("parent_id"))
    try:
        contacts = [x for x in c.list_contacts() if x.get("account_id") == account_id]
    except Exception:
        contacts = []
    website = None
    if request.args.get("pid"):
        p = store.get(int(request.args["pid"]))
        website = p and p["evidence"].get("website")
    elif request.args.get("slug"):
        m = next((m for m in (store.last_run() or {}).get("matches", []) if m["slug"] == request.args["slug"]), {})
        website = m.get("website")
    snap = present.record_snapshot(a, parent, contacts)
    links = {"chow": (a.get("chow_current_account"), name_of(a.get("chow_current_account"))),
             "dup": (a.get("duplicate_of_account"), name_of(a.get("duplicate_of_account")))}
    return render_template("_record.html", a=a, snap=snap, rows=present.compare(website, [snap]), links=links,
                           website=website, practice=config.PRACTICE, crm_url=crm_link(account_id),
                           chips=present.CHIPS, money=present.money, short_id=present.short_id,
                           updated=present.when(a.get("updated_at")))


# ---------------------------------------------------------------- actions
@app.post("/run")
def run_now():
    try:
        run_once(lambda m: None)
        flash(run_summary(), "ok")
    except Exception as e:  # show the problem in the page instead of a 500
        flash(f"Couldn't refresh: {e}", "bad")
    return redirect(url_for("index"))


@app.post("/p/<int:pid>/approve")
def approve(pid):
    p = store.get(pid)
    if not p or p["status"] not in ("pending", "failed"):
        flash("That suggestion has already been decided.", "bad")
        return redirect(url_for("index"))
    name, _ = present.name_and_place(p)
    note = request.form.get("note", "")
    try:
        result = apply_proposal(crm(), p, REVIEWER, save_progress=lambda r: store.save_partial_result(pid, r))
        store.decide(pid, "applied", REVIEWER, note, result=result)
        where = "practice copy" if config.PRACTICE else "CRM"
        flash(f"Approved: {name}. The change is in the {where} and was read back to confirm it.", "ok")
        return redirect(page_link("history", pid))
    except (ApplyError, CRMError) as e:
        store.decide(pid, "failed", REVIEWER, note, result=store.get(pid)["result"], error=str(e))
        flash(f"Couldn't apply {name}. Nothing was changed. {e}", "bad")
        return redirect(page_link("queue", pid))


@app.post("/p/<int:pid>/reject")
def reject(pid):
    p = store.get(pid)
    if p and p["status"] in ("pending", "failed"):
        store.decide(pid, "rejected", REVIEWER, request.form.get("note", ""))
        flash(f"Rejected: {present.name_and_place(p)[0]}. Nothing was changed in the CRM.", "ok")
    return redirect(page_link("history", pid))


# ------------------------------------------------------------ practice only
@app.post("/practice/reset")
def practice_reset():
    if not config.PRACTICE:
        abort(404)
    crm().reset()
    store.reset()
    run_once(lambda m: None)
    flash("Started over: the practice copy is back to its original state and every decision is cleared. "
          + run_summary(), "ok")
    return redirect(url_for("index"))


@app.get("/practice-crm/<account_id>")
def practice_account(account_id):
    """Stands in for the sandbox's CRM browser while practising."""
    if not config.PRACTICE:
        abort(404)
    c = crm()
    if account_id not in c.accounts:
        abort(404)
    a = c.accounts[account_id]
    pointing = [x for x in c.accounts.values()
                if account_id in (x.get("chow_current_account"), x.get("duplicate_of_account"), x.get("parent_id"))]
    contacts = [x for x in c.contacts if x.get("account_id") == account_id]
    return render_template("practice_account.html", a=a, accounts=c.accounts, pointing=pointing,
                           contacts=contacts, money=present.money, when=present.when)


if __name__ == "__main__":
    if config.PRACTICE:
        print("PRACTICE MODE: changes go to a copy of the CRM saved on this computer, not your real CRM.")
        if store.last_run() is None:
            run_once(print)
    else:
        print("LIVE MODE: Approve writes to your assessment CRM.")
    port = int(os.environ.get("PORT", 5001 if config.PRACTICE else 5000))
    print(f"Open http://127.0.0.1:{port} in Chrome. Keep this window open while you use the app.")
    app.run(debug=False, port=port)
