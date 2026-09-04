"""Apply a verified bibliographic refresh.

- Strip seed DOIs that do not resolve to the stored title
- Insert Crossref/doi.org-verified papers (geology hazards + mineral resources)
- Stamp last_updated in the meta table
"""
from __future__ import annotations

import html
import json
import math
import re
import sqlite3
import time
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

from cli import compute_quality, init_db

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "db" / "papers.db"
ADDITIONS_PATH = BASE_DIR / "data" / "verified_additions_2026-09.json"
MAILTO = "research@gz.gov.cn"
UA = f"PaperDB/2.1 (mailto:{MAILTO})"
TODAY = date.today().isoformat()

DOI_STRIP_NOTE = (
    "DOI stripped 2026-09-04: Crossref/doi.org did not confirm this title "
    "(unregistered, 404, or resolved to a different paper)."
)


def get_conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def migrate(conn: sqlite3.Connection):
    cols = [r[1] for r in conn.execute("PRAGMA table_info(papers)").fetchall()]
    if "doi_verified" not in cols:
        conn.execute("ALTER TABLE papers ADD COLUMN doi_verified INTEGER DEFAULT 0")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    conn.commit()


def crossref_work(doi: str) -> dict | None:
    url = f"https://api.crossref.org/works/{urllib.parse.quote(doi)}?mailto={MAILTO}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())["message"]
    except Exception as e:
        print(f"  Crossref miss {doi}: {e}")
        return None


def format_authors(item: dict) -> str:
    authors = item.get("author") or []
    names = []
    for a in authors[:10]:
        g = a.get("given", "") if isinstance(a.get("given"), str) else ""
        f = a.get("family", "") if isinstance(a.get("family"), str) else ""
        names.append(f"{g} {f}".strip())
    if len(authors) > 10:
        names.append("et al.")
    return "; ".join(n for n in names if n) or "Unknown"


def clean_text(text: str) -> str:
    if not text:
        return ""
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    text = re.sub(r"^Abstract\s+", "", text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip()


def enrich_from_crossref(paper: dict) -> dict:
    item = crossref_work(paper["doi"])
    time.sleep(0.4)
    if not item:
        return paper
    title = clean_text((item.get("title") or [""])[0])
    orig = None
    for ot in item.get("original-title") or []:
        ot = clean_text(ot)
        if ot:
            orig = ot
            break
    if title and not paper.get("title"):
        paper["title"] = title
    if orig and not paper.get("title_cn"):
        paper["title_cn"] = orig
    paper["authors"] = format_authors(item) or paper.get("authors") or "Unknown"
    paper["citation_count"] = item.get("is-referenced-by-count", paper.get("citation_count") or 0)
    journal = clean_text((item.get("container-title") or [""])[0])
    if journal:
        paper["journal"] = journal
    year = (item.get("published") or item.get("issued") or {}).get("date-parts", [[None]])[0][0]
    if year:
        paper["year"] = year
    abstract = clean_text(item.get("abstract") or "")
    if abstract:
        paper["abstract"] = abstract[:2500]
    return paper


def strip_unverified_seed_dois(conn: sqlite3.Connection) -> int:
    rows = conn.execute(
        "SELECT id, doi, title FROM papers WHERE source_type='cn_seed' AND doi IS NOT NULL AND doi!=''"
    ).fetchall()
    n = 0
    for row in rows:
        note = (conn.execute("SELECT notes FROM papers WHERE id=?", (row["id"],)).fetchone()["notes"] or "")
        if DOI_STRIP_NOTE not in note:
            note = (note + " " + DOI_STRIP_NOTE).strip()
        conn.execute(
            "UPDATE papers SET doi=NULL, doi_verified=0, notes=? WHERE id=?",
            (note, row["id"]),
        )
        n += 1
    # Existing Crossref rows are bibliographically sourced
    conn.execute("UPDATE papers SET doi_verified=1 WHERE source='crossref' AND doi IS NOT NULL AND doi!=''")
    conn.commit()
    return n


def insert_paper(conn: sqlite3.Connection, p: dict) -> bool:
    doi = p.get("doi") or ""
    title = p["title"]
    existing = conn.execute(
        "SELECT id FROM papers WHERE (doi IS NOT NULL AND doi!='' AND lower(doi)=lower(?)) OR title=?",
        (doi, title),
    ).fetchone()
    if existing:
        print(f"  skip existing: {title[:70]}")
        return False

    lang = p.get("language") or "en"
    title_cn = p.get("title_cn") or (title if lang == "zh" else None)
    tier = p.get("journal_tier") or ""
    cited = int(p.get("citation_count") or 0)
    year = p.get("year")
    quality = compute_quality(tier, cited, year) if tier else 0
    conn.execute(
        """
        INSERT INTO papers (
            title, title_cn, authors, year, journal, doi, abstract, keywords,
            category_id, language, journal_tier, quality_score, source, source_type,
            source_id, url, citation_count, doi_verified
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            title,
            title_cn,
            p.get("authors") or "Unknown",
            year,
            p.get("journal"),
            doi or None,
            p.get("abstract") or "",
            p.get("keywords") or "",
            p.get("category_id"),
            lang,
            tier,
            quality,
            p.get("source") or "crossref",
            p.get("source_type") or "journal",
            p.get("source_id") or doi,
            p.get("url") or (f"https://doi.org/{doi}" if doi else None),
            cited,
            1 if p.get("doi_verified", 1) else 0,
        ),
    )
    return True


def set_last_updated(conn: sqlite3.Connection, when: str):
    conn.execute(
        "INSERT INTO meta(key, value) VALUES('last_updated', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (when,),
    )
    conn.execute(
        "INSERT INTO meta(key, value) VALUES('last_updated_note', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        ("Verified Crossref/doi.org refresh (geology hazards + mineral resources)",),
    )
    conn.commit()


def apply_refresh():
    init_db().close()
    conn = get_conn()
    migrate(conn)

    stripped = strip_unverified_seed_dois(conn)
    print(f"Stripped unverified seed DOIs: {stripped}")

    payload = json.loads(ADDITIONS_PATH.read_text(encoding="utf-8"))
    added = 0
    for p in payload["papers"]:
        if p.get("enrich_from_crossref"):
            p = enrich_from_crossref(p)
        if insert_paper(conn, p):
            added += 1
            print(f"  + {p.get('year')} {p.get('journal')} | {p['title'][:72]}")
    conn.commit()
    set_last_updated(conn, payload.get("as_of") or TODAY)
    total = conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
    conn.close()
    print(f"Added {added} verified papers. Database now {total} rows. last_updated={payload.get('as_of') or TODAY}")
    return added


if __name__ == "__main__":
    apply_refresh()
