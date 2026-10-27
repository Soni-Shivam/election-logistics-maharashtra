"""Registered electors and votes polled per Maharashtra Assembly constituency, 2009-2024, from Wikipedia.

The 2008 delimitation has been in force for every Assembly election since 2009, so the
electors on each seat's page show how the map has drifted on real electoral rolls,
with no reconstruction of boundaries. Each constituency page carries one
{{Election box begin}} per election with a ``reg. electors`` field; this script reads
those through the MediaWiki API (50 pages per request) and writes one row per AC.

District comes from the AC number: seats are numbered in district order, so the
cumulative seat counts in data/maharashtra_ac_2008.csv give each district's range.
Palghar (created 2014) is kept inside Thane, as it was when the map was drawn.

Wikipedia is a secondary source; spot-check figures against ECI statistical reports
before quoting them.

Example:
    python fetch_ac_electors.py --out data/maharashtra_ac_electors_wikipedia.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
API = "https://en.wikipedia.org/w/api.php"
LIST_PAGE = "List_of_constituencies_of_the_Maharashtra_Legislative_Assembly"
AGENT = "ie301-election-logistics/1.0 (course research; python urllib)"
YEARS = (2009, 2014, 2019, 2024)


def api(params: dict) -> dict:
    url = API + "?" + urllib.parse.urlencode({**params, "format": "json", "formatversion": 2})
    req = urllib.request.Request(url, headers={"User-Agent": AGENT})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def constituency_titles() -> dict[int, tuple[str, str]]:
    text = api({"action": "parse", "page": LIST_PAGE, "prop": "wikitext"})["parse"]["wikitext"]
    out = {}
    for m in re.finditer(r"\n\|\s*(\d{1,3})\s*\n\|\s*\[\[([^\]|]+)\|?([^\]]*)\]\]", text):
        no = int(m.group(1))
        if 1 <= no <= 288 and no not in out:
            out[no] = (m.group(2).strip(), (m.group(3) or m.group(2)).strip())
    return out


def districts() -> dict[int, str]:
    out, start = {}, 1
    with (ROOT / "data/maharashtra_ac_2008.csv").open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            name = "Thane" if row["district"].strip() == "Palghar" else row["district"].strip()
            for no in range(start, start + int(row["seats"])):
                out[no] = name
            start += int(row["seats"])
    return out


def electors_by_year(text: str) -> dict[int, int]:
    return box_values(text)[0]


def box_values(text: str) -> tuple[dict[int, int], dict[int, int]]:
    """(electors, votes polled) per election year from the page's election boxes."""
    found, votes = {}, {}
    for chunk in re.split(r"\{\{\s*Election box begin", text, flags=re.I)[1:]:
        head = chunk[:400]
        if "Lok Sabha" in head or "General election" in head:
            continue
        year = re.search(r"\b(19[5-9]\d|20[0-3]\d)\b", head)
        reg = re.search(r"reg\.?\s*electors\s*=\s*([\d,]+)", chunk, flags=re.I)
        turn = re.search(r"Election box turnout\s*\|\s*votes\s*=\s*([\d,]+)", chunk, flags=re.I)
        if year and reg and int(year.group(1)) in YEARS and int(year.group(1)) not in found:
            found[int(year.group(1))] = int(reg.group(1).replace(",", ""))
            if turn:
                votes[int(year.group(1))] = int(turn.group(1).replace(",", ""))
    return found, votes


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=ROOT / "data/maharashtra_ac_electors_wikipedia.csv")
    a = ap.parse_args()

    titles = constituency_titles()
    dist = districts()
    missing = sorted(set(range(1, 289)) - set(titles))
    print(f"{len(titles)} constituencies in the list page; missing numbers: {missing}")

    pages: dict[str, str] = {}
    redirects: dict[str, str] = {}
    names = sorted({t for t, _ in titles.values()})
    for i in range(0, len(names), 50):
        res = api({"action": "query", "prop": "revisions", "rvprop": "content", "rvslots": "main",
                   "redirects": 1, "titles": "|".join(names[i:i + 50])})["query"]
        for r in res.get("redirects", []):
            redirects[r["from"]] = r["to"]
        for n in res.get("normalized", []):
            redirects.setdefault(n["from"], n["to"])
        for p in res["pages"]:
            if "revisions" in p:
                pages[p["title"]] = p["revisions"][0]["slots"]["main"]["content"]
        time.sleep(0.5)

    rows = []
    for no in sorted(titles):
        title, label = titles[no]
        resolved = redirects.get(title, title)
        resolved = redirects.get(resolved, resolved)
        text = pages.get(resolved, "")
        reserved = "SC" if re.search(r"reserved[^\n]*\bSC\b|\|\s*reservation\s*=\s*SC", text) else \
                   "ST" if re.search(r"reserved[^\n]*\bST\b|\|\s*reservation\s*=\s*ST", text) else ""
        e, v = box_values(text)
        rows.append({"ac_no": no, "name": label, "district_2008": dist.get(no, ""), "reserved": reserved,
                     "wikipedia_title": resolved, **{f"electors_{y}": e.get(y, "") for y in YEARS},
                     **{f"votes_{y}": v.get(y, "") for y in YEARS}})
    a.out.parent.mkdir(parents=True, exist_ok=True)
    with a.out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    for y in YEARS:
        print(f"electors_{y}: {sum(r[f'electors_{y}'] != '' for r in rows)} of {len(rows)} seats; "
              f"votes_{y}: {sum(r[f'votes_{y}'] != '' for r in rows)}")


if __name__ == "__main__":
    main()
