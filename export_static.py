"""Export SQLite papers to the static GitHub Pages payload (docs/)."""
from __future__ import annotations

import json
import shutil
from datetime import date
from pathlib import Path

from cli import get_conn, get_stats

BASE_DIR = Path(__file__).parent
DOCS_DIR = BASE_DIR / "docs"
DATA_DIR = DOCS_DIR / "data"
TEMPLATE = BASE_DIR / "templates" / "static.html"


def meta_value(conn, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    if not row:
        return default
    return row[0] if not isinstance(row, dict) else row["value"]


def export_static():
    conn = get_conn()
    conn.row_factory = None
    import sqlite3
    conn.row_factory = sqlite3.Row

    papers = [dict(r) for r in conn.execute("""
        SELECT p.*, c.name as category_name
        FROM papers p LEFT JOIN categories c ON p.category_id = c.id
        ORDER BY CASE WHEN p.language='zh' THEN 0 ELSE 1 END,
                 COALESCE(p.citation_count, 0) DESC, p.id
    """).fetchall()]
    categories = [dict(r) for r in conn.execute("""
        SELECT c.id, c.name, COUNT(p.id) as paper_count
        FROM categories c LEFT JOIN papers p ON c.id = p.category_id
        GROUP BY c.id ORDER BY c.id
    """).fetchall()]

    s = get_stats()
    with_title_cn = conn.execute(
        "SELECT COUNT(*) FROM papers WHERE title_cn IS NOT NULL AND title_cn!=''"
    ).fetchone()[0]
    last_updated = meta_value(conn, "last_updated", date.today().isoformat())
    conn.close()

    by_tier = [{"tier": t, "count": c} for t, c in s.get("by_tier") or []]
    stats = {
        "total": s["total"],
        "downloaded": s["downloaded"],
        "cn_count": s["cn_count"],
        "en_count": s["en_count"],
        "by_category": [
            {"id": c["id"], "name": c["name"], "count": c["paper_count"]}
            for c in categories
        ],
        "by_tier": by_tier,
        "last_updated": last_updated,
        "with_title_cn": with_title_cn,
    }

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "papers.json").write_text(
        json.dumps(papers, ensure_ascii=False, default=str), encoding="utf-8"
    )
    (DATA_DIR / "categories.json").write_text(
        json.dumps(categories, ensure_ascii=False), encoding="utf-8"
    )
    (DATA_DIR / "stats.json").write_text(
        json.dumps(stats, ensure_ascii=False), encoding="utf-8"
    )
    shutil.copyfile(TEMPLATE, DOCS_DIR / "index.html")
    print(f"Exported {len(papers)} papers → {DOCS_DIR} (last_updated={last_updated})")
    return stats


if __name__ == "__main__":
    export_static()
