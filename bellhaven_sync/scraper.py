"""Scrape every Bellhaven community from the public website.

The directory (/communities, paginated) is not the whole truth: a newly
acquired community can be linked from the home page before it is added to
the directory. So instead of reading only the directory, we crawl every
internal page and collect every /communities/<slug> link we find, then
record where each one was found.
"""
import re
from collections import deque
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

DETAIL_RE = re.compile(r"^/communities/[a-z0-9-]+/?$")
SKIP_PREFIXES = ("/api", "/crm", "/static")
ADDRESS_LAST_LINE = re.compile(r"^(?P<city>.+?),\s*(?P<state>[A-Z]{2})\s+(?P<zip>\d{5})(?:-\d{4})?$")


def _internal_path(base_url, href):
    if not href or href.startswith(("mailto:", "tel:", "#", "javascript:")):
        return None
    absolute = urljoin(base_url + "/", href)
    parsed = urlparse(absolute)
    if parsed.netloc != urlparse(base_url).netloc:
        return None
    path = parsed.path or "/"
    if path.startswith(SKIP_PREFIXES):
        return None
    return path + (f"?{parsed.query}" if parsed.query else "")


def crawl_community_links(session, base_url, max_pages=200):
    """Return {detail_path: set(of pages it was linked from)}."""
    queue, seen, found = deque(["/"]), set(), {}
    queue.append("/communities")
    while queue and len(seen) < max_pages:
        path = queue.popleft()
        if path in seen:
            continue
        seen.add(path)
        resp = session.get(base_url + path, timeout=30)
        if resp.status_code != 200 or "text/html" not in resp.headers.get("content-type", ""):
            continue
        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.find_all("a", href=True):
            target = _internal_path(base_url, a["href"])
            if not target:
                continue
            bare = target.split("?")[0].rstrip("/") or "/"
            if DETAIL_RE.match(bare):
                found.setdefault(bare, set()).add(path)
            elif target not in seen:
                queue.append(target)
    return found


def parse_address(text):
    """'210 Orchard Lane | Maplewood, OH 44280' -> dict."""
    parts = [p.strip() for p in (text or "").split("|") if p.strip()]
    street = parts[0] if parts else ""
    city = state = zip_code = ""
    if len(parts) > 1:
        m = ADDRESS_LAST_LINE.match(parts[-1])
        if m:
            city, state, zip_code = m.group("city"), m.group("state"), m.group("zip")
    return {"street": street, "city": city, "state": state, "zip": zip_code}


def parse_detail_html(html):
    """Return (name, {label: text}, [care offerings]) from a community page."""
    soup = BeautifulSoup(html, "html.parser")
    name = soup.find("h1").get_text(strip=True) if soup.find("h1") else ""
    fields, offerings = {}, []
    dl = soup.find("dl", class_="detail")
    if dl:
        for dt in dl.find_all("dt"):
            dd = dt.find_next_sibling("dd")
            if not dd:
                continue
            label = dt.get_text(strip=True)
            for br in dd.find_all("br"):
                br.replace_with(" | ")
            fields[label] = re.sub(r"\s+", " ", dd.get_text(" ")).strip()
            if label.lower().startswith("care"):
                offerings = [b.get_text(strip=True) for b in dd.select(".badge")]
    return name, fields, offerings


def location_from_fields(name, fields, offerings, path, linked_from):
    addr = parse_address(fields.get("Address", ""))
    return {
        "slug": path.rstrip("/").split("/")[-1],
        "url": path,
        "name": name,
        "street": addr["street"],
        "city": addr["city"],
        "state": addr["state"],
        "zip": addr["zip"],
        "care_offerings": offerings,
        "administrator": fields.get("Administrator", ""),
        "phone": fields.get("Phone", ""),
        "in_directory": any(p == "/communities" or p.startswith("/communities?") for p in linked_from),
        "linked_from": sorted(linked_from),
    }


def scrape_locations(base_url, session=None):
    session = session or requests.Session()
    links = crawl_community_links(session, base_url)
    locations = []
    for path in sorted(links):
        resp = session.get(base_url + path, timeout=30)
        resp.raise_for_status()
        name, fields, offerings = parse_detail_html(resp.text)
        locations.append(location_from_fields(name, fields, offerings, path, links[path]))
    return locations
