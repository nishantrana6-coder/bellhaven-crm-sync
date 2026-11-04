"""Settings. Override any of these with environment variables."""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BASE_URL = os.environ.get(
    "BH_BASE_URL", "https://analyst-assessment-production.up.railway.app"
).rstrip("/")
API_URL = BASE_URL + "/api/v1"


def api_token():
    """Token from BH_API_TOKEN, or from a local .bh_token file (gitignored)."""
    token = os.environ.get("BH_API_TOKEN", "").strip()
    if not token:
        path = os.path.join(ROOT, ".bh_token")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                token = f.read().strip()
    if not token:
        raise SystemExit("Set BH_API_TOKEN or put the token in a .bh_token file.")
    return token


# The parent account every Bellhaven community should roll up to.
BELLHAVEN_PARENT_NAME = "Bellhaven Senior Living (Parent Account)"

# Practice mode (run_practice.bat): the app uses a pretend copy of the CRM saved
# on this computer instead of the real one. See practice.py.
PRACTICE = os.environ.get("BH_PRACTICE") == "1"

DB_PATH = os.environ.get("BH_DB_PATH", os.path.join(ROOT, "data", "practice.db" if PRACTICE else "pipeline.db"))

# Who is approving, recorded on every decision and in CRM notes.
REVIEWER = os.environ.get("BH_REVIEWER", "reviewer")

# Matching thresholds (see matcher.py for how they are used).
STREET_NAME_MIN_SIMILARITY = 0.85   # same house number + street name this similar
NAME_FALLBACK_MIN_SIMILARITY = 0.85  # same city/state + facility name this similar
NEAR_MISS_MIN_SIMILARITY = 0.50     # shown to the reviewer as "not matched, but similar"
