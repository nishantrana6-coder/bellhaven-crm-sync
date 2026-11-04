"""Text normalization used for matching website locations to CRM accounts.

Everything here is deterministic and dependency free, so a reviewer can
read exactly why two records were (or were not) considered the same place.
"""
import re
from difflib import SequenceMatcher

# Street suffixes and directions, mapped to one canonical short form so
# "4850 Northwest Sylvania Avenue" and "4850 NW Sylvania Ave" compare equal.
STREET_WORDS = {
    "street": "st", "st": "st",
    "avenue": "ave", "ave": "ave", "av": "ave",
    "road": "rd", "rd": "rd",
    "drive": "dr", "dr": "dr",
    "lane": "ln", "ln": "ln",
    "boulevard": "blvd", "blvd": "blvd",
    "parkway": "pkwy", "pkwy": "pkwy",
    "pike": "pike", "pk": "pike", "pke": "pike",
    "court": "ct", "ct": "ct",
    "place": "pl", "pl": "pl",
    "highway": "hwy", "hwy": "hwy",
    "circle": "cir", "cir": "cir",
    "terrace": "ter", "ter": "ter",
    "way": "way",
    "north": "n", "n": "n",
    "south": "s", "s": "s",
    "east": "e", "e": "e",
    "west": "w", "w": "w",
    "northwest": "nw", "nw": "nw",
    "northeast": "ne", "ne": "ne",
    "southwest": "sw", "sw": "sw",
    "southeast": "se", "se": "se",
}

# Name variants that mean the same thing ("Rehab" vs "Rehabilitation").
NAME_WORDS = {
    "rehab": "rehabilitation",
    "centre": "center",
    "healthcare": "health care",
    "ctr": "center",
    "&": "and",
}
NAME_STOPWORDS = {"the", "of", "at", "and"}

# Website care offerings -> CRM care_type picklist values.
CARE_MAP = {
    "assisted living": "Assisted Living",
    "memory support": "Memory Care",
    "memory care": "Memory Care",
    "short-term rehabilitation & nursing": "Skilled Nursing",
    "short-term rehabilitation and nursing": "Skilled Nursing",
    "skilled nursing": "Skilled Nursing",
    "independent living": "Independent Living",
}


def clean(text):
    """Lowercase, '&' -> 'and', strip punctuation, collapse whitespace."""
    text = (text or "").lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def norm_street(street):
    tokens = clean(street).split()
    return " ".join(STREET_WORDS.get(t, t) for t in tokens)


def street_number(street):
    m = re.match(r"\s*(\d+)", street or "")
    return m.group(1) if m else ""


def street_name(street):
    """Normalized street without the house number."""
    return re.sub(r"^\d+\s*", "", norm_street(street))


def is_po_box(street):
    return bool(re.match(r"\s*p\.?\s*o\.?\s*box\b", street or "", re.I))


def norm_name(name):
    text = (name or "").lower()
    for src, dst in NAME_WORDS.items():
        text = re.sub(rf"(?<![a-z]){re.escape(src)}(?![a-z])", f" {dst} ", text)
    tokens = [t for t in clean(text).split() if t not in NAME_STOPWORDS]
    return " ".join(tokens)


def similarity(a, b):
    return SequenceMatcher(None, a or "", b or "").ratio()


def name_similarity(a, b):
    return similarity(norm_name(a), norm_name(b))


def digits(phone):
    return re.sub(r"\D", "", phone or "")


def map_care(offerings):
    """Map website offerings to CRM care types, preserving website order."""
    out = []
    for o in offerings or []:
        mapped = CARE_MAP.get(o.strip().lower())
        if mapped and mapped not in out:
            out.append(mapped)
    return out
