"""
中文论文种子数据导入 + 英文标题批量翻译
使用 MyMemory API (免费，en-GB→zh-CN)
"""
import sqlite3
import json
import time
import sys
from pathlib import Path
from deep_translator import MyMemoryTranslator

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "db" / "papers.db"
SEED_PATH = BASE_DIR / "cn_papers_seed.json"

SLEEP = 0.5  # MyMemory free tier: ~10 req/min


def get_conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def import_cn_seed():
    """导入中文种子论文"""
    with open(SEED_PATH, "r", encoding="utf-8") as f:
        papers = json.load(f)

    conn = get_conn()
    added = skipped = 0

    for p in papers:
        title = p["title"]
        # 检查去重
        existing = conn.execute(
            "SELECT id FROM papers WHERE title=? OR (doi IS NOT NULL AND doi=? AND doi!='')",
            (title, p["doi"])
        ).fetchone()
        if existing:
            skipped += 1
            continue

        conn.execute("""
            INSERT INTO papers (title, title_cn, authors, year, journal, doi,
                abstract, keywords, category_id, language, journal_tier,
                source, source_type, citation_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'zh', ?,
                'manual_cn', 'cn_seed', ?)
        """, (
            title, title, p["authors"], p["year"], p["journal"],
            p["doi"] or None, p["abstract"], p["keywords"],
            p["category_id"], p["journal_tier"], p["citation_count"]
        ))

        # 同步FTS
        conn.execute("""
            INSERT INTO papers_fts(rowid, title, title_cn, authors, abstract, keywords, journal)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (conn.execute("SELECT last_insert_rowid()").fetchone()[0],
              title, title, p["authors"], p["abstract"], p["keywords"], p["journal"]))

        added += 1

    conn.commit()
    conn.close()
    print(f"✅ 中文种子论文: +{added} 篇, 跳过 {skipped} 篇")
    return added


def translate_titles(batch_size=20, dry_run=False):
    """翻译英文论文标题为中文"""
    conn = get_conn()

    # 找出所有需要翻译的英文论文 (title_cn为空或等于title表示未翻译)
    rows = conn.execute("""
        SELECT id, title FROM papers
        WHERE language='en' AND (title_cn IS NULL OR title_cn='' OR title_cn=title)
        ORDER BY id
    """).fetchall()

    if not rows:
        print("✅ 所有英文论文已有中文标题")
        conn.close()
        return 0

    print(f"📝 待翻译: {len(rows)} 篇\n")

    translated = 0
    failed = 0
    total_chars = 0

    for i, (pid, title) in enumerate(rows):
        # 清理标题
        title_clean = title.strip()
        if not title_clean or len(title_clean) < 5:
            continue

        # MyMemory 免费限制: 500 chars/request
        if len(title_clean) > 400:
            title_clean = title_clean[:400]

        try:
            result = MyMemoryTranslator(
                source='en-GB', target='zh-CN'
            ).translate(title_clean)

            if result and result != title_clean:
                if not dry_run:
                    conn.execute(
                        "UPDATE papers SET title_cn=? WHERE id=?",
                        (result, pid)
                    )
                translated += 1
                total_chars += len(title_clean)
                if i % 10 == 0 or i == len(rows) - 1:
                    print(f"  [{i+1}/{len(rows)}] {title_clean[:50]}... → {result[:50]}")
            else:
                failed += 1

        except Exception as e:
            failed += 1
            if i % 20 == 0:
                print(f"  ⚠️ [{i+1}/{len(rows)}] {title_clean[:40]}... 失败: {str(e)[:60]}")

        time.sleep(SLEEP)

        # 每 50 篇暂停一下，避免被限
        if translated > 0 and translated % 50 == 0:
            print(f"  ⏸️  已翻译 {translated} 篇，暂停5秒...")
            time.sleep(5)

    if not dry_run:
        conn.commit()
        # 同步 FTS
        conn.execute("INSERT INTO papers_fts(papers_fts) VALUES('rebuild')")

    conn.close()

    print(f"\n{'[DRY RUN] ' if dry_run else ''}✅ 翻译完成: {translated} 篇, 失败 {failed}, 共 {total_chars} 字符\n")
    return translated


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"

    if cmd == "import" or cmd == "all":
        print("=== 导入中文种子论文 ===\n")
        import_cn_seed()

    if cmd == "translate" or cmd == "all":
        print("\n=== 翻译英文标题 ===\n")
        translate_titles(dry_run=False)
