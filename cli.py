"""
高端论文数据库 v2 — 期刊分级 & 质量过滤
基于广州市地质调查院（海洋发展促进中心）职能体系
数据源：CrossRef(顶级期刊定向) + arXiv(预印本) + Unpaywall(OA PDF)
质量标准：S/A/B/C四级期刊 + 引用量 + 时效性加权
"""

import sqlite3
import json
import os
import sys
import time
import urllib.request
import urllib.parse
import urllib.error
import ssl
import re
import xml.etree.ElementTree as ET
import math
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "db" / "papers.db"
PDF_DIR = BASE_DIR / "papers"
SCHEMA_PATH = BASE_DIR / "schema.sql"
JOURNALS_PATH = BASE_DIR / "journals.json"

MAILTO = "research@gz.gov.cn"
SLEEP_BETWEEN = 0.8  # CrossRef礼貌间隔
ARXIV_SLEEP = 3.0     # arXiv更严格

# ============ 期刊分级加载 ============
_journals_data = None


def load_journals():
    global _journals_data
    if _journals_data is None:
        with open(JOURNALS_PATH, "r", encoding="utf-8") as f:
            _journals_data = json.load(f)
    return _journals_data


def _tier_scores():
    """tier -> numeric score"""
    jd = load_journals()
    return {t: v["score"] for t, v in jd["tiers"].items()}


def _journal_map():
    """Build: issn -> (name, tier, cats) for English journals"""
    jd = load_journals()
    m = {}
    for tier, journals in jd["journals"].items():
        for j in journals:
            issn = j["issn"]
            m[issn] = (j["name"], tier, j["cats"])
    # Chinese journals via cn_journals
    for tier, journals in jd.get("cn_journals", {}).items():
        for j in journals:
            m[j["issn"]] = (j["name"], tier, j["cats"])
    return m


def compute_quality(tier, citation_count, year):
    """综合质量分: tier基础分 * log(引用+1) * 时效系数"""
    scores = _tier_scores()
    base = scores.get(tier, 1.0)
    cit_factor = math.log(citation_count + 1.5)
    if year and year >= 2025:
        recency = 1.3
    elif year and year >= 2023:
        recency = 1.1
    else:
        recency = 1.0
    return round(base * cit_factor * recency, 2)


# ============ 工具函数 ============
def init_db():
    conn = sqlite3.connect(str(DB_PATH))
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        conn.executescript(f.read())
    conn.commit()
    migrate_db(conn)
    return conn


def migrate_db(conn):
    cols = [r[1] for r in conn.execute("PRAGMA table_info(papers)").fetchall()]
    for col in ["language", "journal_tier", "quality_score", "source_type"]:
        if col not in cols:
            conn.execute(f"ALTER TABLE papers ADD COLUMN {col} TEXT DEFAULT ''" if col != 'quality_score' else
                         f"ALTER TABLE papers ADD COLUMN {col} REAL DEFAULT 0")
            conn.commit()
            print(f"  ✅ 已添加 {col} 列")


def get_conn():
    return sqlite3.connect(str(DB_PATH))


def is_chinese_text(text):
    if not text:
        return False
    return len(re.findall(r'[\u4e00-\u9fff]', text)) > 2


def clean_html(text):
    if not text:
        return ""
    return re.sub(r'<[^>]+>', '', text).strip()


def safe_filename(text, maxlen=80):
    return re.sub(r'[\\/*?:"<>|]', "", text)[:maxlen]


# ============ Crossref API ============
CROSSREF_BASE = "https://api.crossref.org/works"
UNPAYWALL_BASE = "https://api.unpaywall.org/v2"


def crossref_search_by_issn(issn, rows=15, year_from=2022):
    """按ISSN搜索特定期刊论文（高引用优先）"""
    params = {
        "rows": rows,
        "filter": f"from-pub-date:{year_from}-01-01,type:journal-article,issn:{issn}",
        "sort": "is-referenced-by-count",
        "order": "desc",
        "mailto": MAILTO,
    }
    url = f"{CROSSREF_BASE}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": f"PaperDB/2.0 (mailto:{MAILTO})"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        return data.get("message", {}).get("items", [])
    except Exception as e:
        if hasattr(e, 'code') and e.code == 429:
            print(f"  ⚠️ Crossref 限流，等待5秒...")
            time.sleep(5)
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = json.loads(resp.read().decode())
                return data.get("message", {}).get("items", [])
            except:
                return []
        print(f"  Crossref错误 ({issn}): {e}")
        return []


def unpaywall_lookup(doi):
    if not doi:
        return None
    url = f"{UNPAYWALL_BASE}/{doi}?email={MAILTO}"
    req = urllib.request.Request(url, headers={"User-Agent": "PaperDB/2.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        if data.get("is_oa"):
            best = data.get("best_oa_location", {})
            return best.get("url_for_pdf") or best.get("url")
    except:
        pass
    return None


def format_crossref_authors(item):
    authors = item.get("author", [])
    if not authors:
        return "Unknown"
    names = []
    for a in authors[:10]:
        g = a.get("given", "") if isinstance(a.get("given"), str) else ""
        f = a.get("family", "") if isinstance(a.get("family"), str) else ""
        names.append(f"{g} {f}".strip())
    if len(authors) > 10:
        names.append("et al.")
    return "; ".join(names)


# ============ arXiv API ============
ARXIV_API = "https://export.arxiv.org/api/query"

ARXIV_QUERIES = {
    1: [
        "landslide monitoring machine learning",
        "geohazard early warning deep learning",
        "InSAR deformation monitoring",
    ],
    4: [
        "marine economy blue growth",
        "coastal management policy",
    ],
    5: [
        "coastal erosion storm surge",
        "marine ecological restoration",
        "harmful algal bloom",
    ],
    6: [
        "urban underground space engineering",
        "subsidence monitoring InSAR",
    ],
    7: [
        "urban planning spatial analysis",
    ],
    8: [
        "remote sensing land cover classification",
        "satellite monitoring earth observation",
    ],
    10: [
        "deep learning remote sensing geology",
        "geospatial foundation model",
        "digital twin ocean",
        "AI earth science",
    ],
}


def arxiv_search(query, max_results=10):
    """搜索arXiv API"""
    params = urllib.parse.urlencode({
        "search_query": query,
        "start": 0,
        "max_results": max_results,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    })
    url = f"{ARXIV_API}?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "PaperDB/2.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = resp.read().decode()
    except Exception as e:
        print(f"  arXiv错误: {e}")
        return []

    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "arxiv": "http://arxiv.org/schemas/atom",
    }
    root = ET.fromstring(data)
    entries = root.findall("atom:entry", ns)
    results = []
    for entry in entries:
        title_el = entry.find("atom:title", ns)
        title = clean_html(title_el.text.strip()) if title_el is not None and title_el.text else ""
        if not title:
            continue

        summary_el = entry.find("atom:summary", ns)
        abstract = summary_el.text.strip() if summary_el is not None and summary_el.text else ""

        published_el = entry.find("atom:published", ns)
        published = published_el.text if published_el is not None else ""

        authors_list = []
        for a in entry.findall("atom:author/atom:name", ns):
            if a.text:
                authors_list.append(a.text)
        authors = "; ".join(authors_list[:10])

        # arXiv ID
        id_el = entry.find("atom:id", ns)
        arxiv_id = id_el.text.split("/abs/")[-1] if id_el is not None and id_el.text else ""

        # PDF URL
        pdf_url = f"https://arxiv.org/pdf/{arxiv_id}" if arxiv_id else ""

        # Category
        cat_el = entry.find("arxiv:primary_category", ns)
        primary_cat = cat_el.get("term", "") if cat_el is not None else ""

        # Year
        year = int(published[:4]) if published and len(published) >= 4 else None

        results.append({
            "title": title,
            "authors": authors,
            "year": year,
            "abstract": abstract,
            "url": pdf_url,
            "source_id": arxiv_id,
            "primary_cat": primary_cat,
        })

    return results


# ============ 核心检索导入 ============

def fetch_elite_papers(category_id=None, rows_per_journal=8, year_from=2023,
                       tiers=None, max_total=None):
    """
    定向检索高质量论文：按期刊ISSN逐刊搜索，质量过滤
    tiers: 要包含的等级，默认 ['S','A']
    """
    if tiers is None:
        tiers = ['S', 'A']
    jd = load_journals()
    jmap = _journal_map()

    conn = get_conn()
    total = 0

    # 收集覆盖指定分类的期刊
    candidates = []; seen = set()
    for tier in tiers:
        journals = jd["journals"].get(tier, [])
        for j in journals:
            if category_id and category_id not in j["cats"]:
                continue
            key = j["issn"]
            if key not in seen:
                candidates.append((j["issn"], j["name"], tier))
                seen.add(key)

    print(f"📚 目标期刊: {len(candidates)} 种 ({'/'.join(tiers)}级)")
    print(f"   每刊取 {rows_per_journal} 篇, year≥{year_from}, 按引用排序\n")

    for i, (issn, jname, tier) in enumerate(candidates):
        if max_total and total >= max_total:
            break

        print(f"  [{i+1}/{len(candidates)}] {jname} ({tier}级)...", end=" ", flush=True)
        items = crossref_search_by_issn(issn, rows=rows_per_journal, year_from=year_from)

        imported = 0
        for item in items:
            title = clean_html(item.get("title", [""])[0] if item.get("title") else "")
            if not title:
                continue

            doi = item.get("DOI", "")
            cited = item.get("is-referenced-by-count", 0)
            year_val = item.get("published", {}).get("date-parts", [[None]])[0][0]

            # 质量过滤：最低引用门槛（2025+可以放行）
            min_cit = jd["tiers"].get(tier, {}).get("min_citations", 0)
            if cited < min_cit and (year_val and year_val < 2025):
                continue

            # 去重
            existing = conn.execute(
                "SELECT id FROM papers WHERE doi=? OR title=?",
                (doi, title)
            ).fetchone()
            if existing:
                continue

            authors = format_crossref_authors(item)
            journal_name = clean_html(item.get("container-title", [""])[0] if item.get("container-title") else "")
            abstract = item.get("abstract", "") or ""
            if abstract:
                abstract = re.sub(r'<[^>]+>', '', abstract).strip()

            # PDF链接
            pdf_url = None
            for link in item.get("link", []):
                ct = link.get("content-type", "")
                lu = link.get("url", "")
                if "pdf" in ct.lower() or "pdf" in lu.lower():
                    pdf_url = lu
                    break
            if not pdf_url and doi:
                pdf_url = unpaywall_lookup(doi)
                time.sleep(0.4)

            # 语言检测
            lang = "zh" if is_chinese_text(title) else "en"
            title_cn = title if lang == "zh" else None

            # 检查原标题（中文期刊的英文翻译）
            orig_titles = item.get("original-title", [])
            if orig_titles:
                orig = clean_html(orig_titles[0])
                if is_chinese_text(orig) and lang == "en":
                    title_cn = orig
                    lang = "zh"

            # 关键词存英文标题
            keywords_str = ""
            if lang == "zh":
                sub_titles = item.get("subtitle", [])
                if sub_titles:
                    sub = clean_html(sub_titles[0])
                    if sub and not is_chinese_text(sub):
                        keywords_str = f"EN:{sub}"

            quality = compute_quality(tier, cited, year_val)

            conn.execute("""
                INSERT INTO papers (title, title_cn, authors, year, journal, doi, abstract,
                    keywords, category_id, language, journal_tier, quality_score,
                    source, source_type, source_id, url, citation_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'crossref', 'journal', ?, ?, ?)
            """, (title, title_cn, authors, year_val, journal_name or jname,
                  doi, abstract, keywords_str, category_id, lang, tier, quality,
                  doi, pdf_url, cited))

            imported += 1
            total += 1

        print(f"+{imported}篇" if imported else "无新增")
        time.sleep(SLEEP_BETWEEN)

    conn.commit()
    conn.close()
    print(f"\n✅ 共导入 {total} 篇高质量论文\n")
    return total


def fetch_arxiv_papers(category_id=None, max_per_cat=10):
    """抓取arXiv最新预印本"""
    conn = get_conn()
    total = 0

    queries = []
    if category_id and category_id in ARXIV_QUERIES:
        queries = ARXIV_QUERIES[category_id]
    elif category_id is None:
        for cid in range(1, 11):
            queries.extend(ARXIV_QUERIES.get(cid, [])[:2])

    print(f"🌐 arXiv 预印本: {len(queries)} 个查询\n")

    for q in queries:
        print(f"  🔍 {q[:70]}...", end=" ", flush=True)
        papers = arxiv_search(q, max_results=max_per_cat)
        imported = 0

        for p in papers:
            # 去重
            existing = conn.execute(
                "SELECT id FROM papers WHERE title=?",
                (p["title"],)
            ).fetchone()
            if existing:
                continue

            lang = "zh" if is_chinese_text(p["title"]) else "en"
            title_cn = p["title"] if lang == "zh" else None
            quality = 2.0  # 预印本基础分，无引用数据

            # 分配到最匹配的分类
            assigned_cat = category_id
            if not assigned_cat:
                assigned_cat = _classify_arxiv(p["primary_cat"], p["title"])

            conn.execute("""
                INSERT INTO papers (title, title_cn, authors, year, abstract, keywords,
                    category_id, language, journal_tier, quality_score,
                    source, source_type, source_id, url, citation_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'preprint', ?, 'arxiv', 'preprint', ?, ?, 0)
            """, (p["title"], title_cn, p["authors"], p["year"], p["abstract"],
                  p["primary_cat"], assigned_cat, lang, quality,
                  p["source_id"], p["url"]))

            imported += 1
            total += 1

        print(f"+{imported}篇")
        time.sleep(ARXIV_SLEEP)

    conn.commit()
    conn.close()
    print(f"\n✅ 共导入 {total} 篇arXiv预印本\n")
    return total


# arXiv目录→分类映射
ARXIV_CAT_MAP = {
    "cs.CV": 10, "cs.AI": 10, "cs.LG": 10, "cs.CE": 10,
    "eess.IV": 10, "eess.SP": 10,
    "physics.geo-ph": 1, "physics.ao-ph": 5,
    "stat.ML": 10, "stat.AP": 10,
}


def _classify_arxiv(cat, title):
    if cat in ARXIV_CAT_MAP:
        return ARXIV_CAT_MAP[cat]
    text = title.lower()
    if any(w in text for w in ["landslide", "earthquake", "geohazard", "debris flow"]):
        return 1
    if any(w in text for w in ["coastal", "ocean", "marine", "sea level"]):
        return 5
    if any(w in text for w in ["urban", "underground", "subsidence", "tunnel"]):
        return 6
    if any(w in text for w in ["remote sensing", "satellite", "land cover"]):
        return 8
    if any(w in text for w in ["economy", "policy", "industry"]):
        return 4
    return 10  # 默认AI交叉


def fetch_cn_elite_papers(category_id=None, rows_per_journal=10, year_from=2022):
    """定向检索中文顶刊论文

    注意：Crossref上很多中文期刊注册的是英文标题，但论文原文是中文。
    策略：只要是中文期刊ISSN的论文，自动标记为'zh'语言。
    如果title本身是中文→存入title_cn；如果是英文→title是Crossref英文标题，尝试从original-title取中文。
    """
    jd = load_journals()
    jmap = _journal_map()

    conn = get_conn()
    total = 0
    skipped_zero = []

    # 按分类筛选中文期刊
    cn_journals = jd.get("cn_journals", {})
    candidates = []
    for tier in ["S", "A"]:
        for j in cn_journals.get(tier, []):
            if category_id and category_id not in j["cats"]:
                continue
            candidates.append((j["issn"], j["name"], tier))

    print(f"🇨🇳 中文顶刊: {len(candidates)} 种 (S/A级)\n")

    for i, (issn, jname, tier) in enumerate(candidates):
        print(f"  [{i+1}/{len(candidates)}] {jname} ({tier}级)...", end=" ", flush=True)
        items = crossref_search_by_issn(issn, rows=rows_per_journal, year_from=year_from)

        if not items:
            skipped_zero.append(jname)
            print("无可索引论文")
            continue

        imported = 0
        for item in items:
            # 标题：优先取original-title（往往是中文原标题），否则取标准title
            title_crossref = clean_html(item.get("title", [""])[0] if item.get("title") else "")
            if not title_crossref:
                continue

            # 查找中文原标题（Crossref的original-title字段）
            title_cn = None
            orig_titles = item.get("original-title", [])
            if orig_titles:
                for ot in orig_titles:
                    ot_clean = clean_html(ot)
                    if is_chinese_text(ot_clean):
                        title_cn = ot_clean
                        break

            # 判断：如果Crossref标题是中文 → title_cn = title
            is_crossref_title_cn = is_chinese_text(title_crossref)
            if is_crossref_title_cn:
                title_cn = title_crossref
                display_title = title_crossref
            else:
                # Crossref标题是英文（常见于中文期刊的双语DOI注册）
                display_title = title_crossref
                if not title_cn:
                    # 从副标题或subtitle找中文
                    for field in ["subtitle"]:
                        vals = item.get(field, [])
                        if vals:
                            t = clean_html(vals[0])
                            if is_chinese_text(t):
                                title_cn = t
                                break

            # 确保至少有标题
            if not display_title:
                continue

            doi = item.get("DOI", "")
            cited = item.get("is-referenced-by-count", 0)
            year_val = item.get("published", {}).get("date-parts", [[None]])[0][0]

            existing = conn.execute(
                "SELECT id FROM papers WHERE doi=? OR title=?",
                (doi, display_title)
            ).fetchone()
            if existing:
                continue

            authors = format_crossref_authors(item)
            journal_name = clean_html(item.get("container-title", [""])[0] if item.get("container-title") else "")
            abstract = item.get("abstract", "") or ""
            if abstract:
                abstract = re.sub(r'<[^>]+>', '', abstract).strip()

            # PDF
            pdf_url = None
            for link in item.get("link", []):
                if "pdf" in link.get("content-type", "").lower() or "pdf" in link.get("url", "").lower():
                    pdf_url = link.get("url")
                    break
            if not pdf_url and doi:
                pdf_url = unpaywall_lookup(doi)
                time.sleep(0.4)

            # 关键词：存英文标题备查（Web界面双语显示用）
            keywords_str = jname
            if not is_crossref_title_cn and title_crossref:
                # 中文期刊但Crossref标题是英文，存起来供双语显示
                keywords_str = f"{jname}|EN:{title_crossref}"

            # 质量评分（中文期刊按C级算）
            quality = compute_quality(tier, cited, year_val)

            conn.execute("""
                INSERT INTO papers (title, title_cn, authors, year, journal, doi, abstract,
                    keywords, category_id, language, journal_tier, quality_score,
                    source, source_type, source_id, url, citation_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'zh', ?, ?, 'crossref', 'journal_cn', ?, ?, ?)
            """, (display_title, title_cn or display_title,
                  authors, year_val, journal_name or jname,
                  doi, abstract, keywords_str, category_id, tier, quality,
                  doi, pdf_url, cited))

            imported += 1
            total += 1

        print(f"+{imported}篇")
        time.sleep(SLEEP_BETWEEN)

    if skipped_zero:
        print(f"\n  ⚠️ 以下期刊Crossref无可索引论文: {', '.join(skipped_zero)}")

    conn.commit()
    conn.close()
    print(f"\n✅ 共导入 {total} 篇中文论文\n")
    return total


def fetch_all_elite(year_from=2023):
    """全面高质量检索：全部10个分类，英中+arXiv"""
    conn = get_conn()
    print("=" * 60)
    print("🚀 高端论文数据库 v2 — 全面检索")
    print("=" * 60)

    grand_total = 0

    for cat_id in range(1, 11):
        cat_name = conn.execute(
            "SELECT name FROM categories WHERE id=?", (cat_id,)
        ).fetchone()
        if not cat_name:
            continue
        cat_name = cat_name[0]

        print(f"\n{'='*60}")
        print(f"📂 分类 [{cat_id}] {cat_name}")
        print(f"{'='*60}")

        # 1. 英文顶级期刊
        print("\n📗 英文顶级期刊 (S/A级)...")
        n = fetch_elite_papers(category_id=cat_id, rows_per_journal=6,
                               year_from=year_from, max_total=30)
        grand_total += n

        # 2. 中文核心期刊
        print("\n📕 中文核心期刊...")
        n_cn = fetch_cn_elite_papers(category_id=cat_id, rows_per_journal=8,
                                     year_from=year_from)
        grand_total += n_cn

    # 3. arXiv 最新预印本（仅AI/遥感/灾害相关分类）
    print("\n🌐 arXiv 最新预印本...")
    n_arxiv = fetch_arxiv_papers(category_id=None, max_per_cat=8)
    grand_total += n_arxiv

    conn.close()
    print(f"\n{'='*60}")
    print(f"🎉 全部完成！总计导入 {grand_total} 篇高端论文")
    print(f"{'='*60}\n")
    return grand_total


# ============ 本地搜索 ============
def search_local(query, category=None, year_from=None, year_to=None,
                 language=None, min_tier=None, limit=20):
    """本地全文检索 + 质量排序"""
    conn = get_conn()
    conn.row_factory = sqlite3.Row

    if query:
        sql = """
            SELECT p.*, c.name as category_name FROM papers p
            LEFT JOIN categories c ON p.category_id = c.id
            WHERE papers_fts MATCH ?
        """
        params = [query]
    else:
        sql = """
            SELECT p.*, c.name as category_name FROM papers p
            LEFT JOIN categories c ON p.category_id = c.id
            WHERE 1=1
        """
        params = []

    if category:
        cat_row = conn.execute(
            "SELECT id FROM categories WHERE name LIKE ?", (f"%{category}%",)
        ).fetchone()
        if cat_row:
            sql += " AND p.category_id = ?"
            params.append(cat_row[0])

    if year_from:
        sql += " AND p.year >= ?"
        params.append(year_from)
    if year_to:
        sql += " AND p.year <= ?"
        params.append(year_to)
    if language:
        sql += " AND p.language = ?"
        params.append(language)
    if min_tier:
        tier_order = {"S": 0, "A": 1, "B": 2, "C": 3}
        allowed = [t for t in tier_order if tier_order[t] <= tier_order.get(min_tier, 99)]
        sql += f" AND p.journal_tier IN ({','.join('?' * len(allowed))})"
        params.extend(allowed)

    # 质量分优先，中文其次
    sql += """
        ORDER BY
            CASE WHEN p.journal_tier='S' THEN 0
                 WHEN p.journal_tier='A' THEN 1
                 WHEN p.journal_tier='B' THEN 2
                 WHEN p.journal_tier='C' THEN 3
                 ELSE 4 END,
            CASE WHEN p.language='zh' THEN 0 ELSE 1 END,
            p.quality_score DESC
        LIMIT ?
    """
    params.append(limit)

    results = conn.execute(sql, params).fetchall()
    cols = [d[0] for d in conn.execute("SELECT * FROM papers LIMIT 0").description]
    conn.close()
    return cols, results


def get_stats():
    conn = get_conn()
    total = conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
    downloaded = conn.execute("SELECT COUNT(*) FROM papers WHERE is_downloaded=1").fetchone()[0]
    cn_count = conn.execute("SELECT COUNT(*) FROM papers WHERE language='zh'").fetchone()[0]
    en_count = conn.execute("SELECT COUNT(*) FROM papers WHERE language='en' OR language IS '' OR language IS NULL").fetchone()[0]
    by_tier = conn.execute("""
        SELECT journal_tier, COUNT(*) FROM papers
        WHERE journal_tier IS NOT NULL AND journal_tier != ''
        GROUP BY journal_tier ORDER BY journal_tier
    """).fetchall()
    by_cat = conn.execute("""
        SELECT c.name, COUNT(*) FROM papers p
        JOIN categories c ON p.category_id = c.id
        GROUP BY p.category_id ORDER BY COUNT(*) DESC
    """).fetchall()
    by_year = conn.execute("""
        SELECT year, COUNT(*) FROM papers
        WHERE year IS NOT NULL
        GROUP BY year ORDER BY year DESC
    """).fetchall()
    by_source = conn.execute("""
        SELECT source_type, COUNT(*) FROM papers
        WHERE source_type IS NOT NULL AND source_type != ''
        GROUP BY source_type
    """).fetchall()
    conn.close()
    return {
        "total": total, "downloaded": downloaded,
        "cn_count": cn_count, "en_count": en_count,
        "by_tier": by_tier, "by_category": by_cat,
        "by_year": by_year, "by_source": by_source,
    }


# ============ PDF下载 ============
def download_pdf(paper_id, force=False):
    conn = get_conn()
    paper = conn.execute(
        "SELECT id, title, url, pdf_path FROM papers WHERE id=? AND url IS NOT NULL",
        (paper_id,)
    ).fetchone()
    if not paper:
        print("论文不存在或无PDF链接")
        conn.close()
        return False

    pid, title, pdf_url, existing_path = paper
    if existing_path and os.path.exists(existing_path) and not force:
        print(f"PDF已存在: {existing_path}")
        conn.close()
        return True

    filename = f"{pid}_{safe_filename(title)}.pdf"
    filepath = PDF_DIR / filename

    try:
        print(f"📥 下载: {title[:40]}...")
        ctx = ssl.create_default_context()
        req = urllib.request.Request(pdf_url, headers={"User-Agent": "PaperDB/2.0"})
        with urllib.request.urlopen(req, context=ctx, timeout=60) as resp:
            with open(filepath, "wb") as f:
                f.write(resp.read())

        conn.execute(
            "UPDATE papers SET pdf_path=?, is_downloaded=1 WHERE id=?",
            (str(filepath), pid)
        )
        conn.commit()
        print(f"✅ 已保存: {filepath}")
        conn.close()
        return True
    except Exception as e:
        print(f"❌ 下载失败: {e}")
        conn.close()
        return False


# ============ 数据库重置 ============
def reset_db():
    """清空论文表，保留分类定义和FTS"""
    conn = get_conn()
    conn.execute("DELETE FROM papers")
    conn.execute("DELETE FROM papers_fts")
    # 重置自增
    conn.execute("DELETE FROM sqlite_sequence WHERE name='papers'")
    conn.commit()
    print("✅ 已清空全部论文数据（分类定义保留）")
    conn.close()


# ============ CLI ============
def cli():
    if len(sys.argv) < 2:
        print("""高端论文数据库 v2 — CLI 工具
质量标准: S(顶刊) > A(Q1) > B(Q2) > C(中文核心) + 引用量 + 时效加权

用法:
  python cli.py init                      初始化/迁移数据库
  python cli.py reset                     清空论文数据（保留分类）
  python cli.py elite [--cat N] [--year 2023]  定向检索顶级期刊论文(英)
  python cli.py elite-cn [--cat N] [--year 2022]  定向检索中文核心论文
  python cli.py arxiv [--cat N]           抓取arXiv最新预印本
  python cli.py fetch-all [--year 2023]   全面检索全部10个分类
  python cli.py search <关键词>           本地全文搜索（质量排序）
  python cli.py list [分类]               按分类浏览
  python cli.py top [N]                   TOP论文（按质量分）
  python cli.py stats                     数据库统计
  python cli.py download <论文ID>          下载论文PDF
  python cli.py download-all               下载所有未下载PDF
  python cli.py categories                 列出分类体系
""")
        return

    cmd = sys.argv[1]

    if cmd == "init":
        conn = init_db()
        print("✅ 数据库初始化完成")
        conn.close()

    elif cmd == "reset":
        reset_db()

    elif cmd == "categories":
        conn = get_conn()
        for r in conn.execute("SELECT id, name, description FROM categories ORDER BY id"):
            print(f"  [{r[0]}] {r[1]}")
            print(f"      {r[2]}")
        conn.close()

    elif cmd == "elite":
        cat_id = None; year_from = 2023; tiers = ['S', 'A']
        args = sys.argv[2:]
        i = 0
        while i < len(args):
            if args[i] == "--cat" and i + 1 < len(args):
                cat_id = int(args[i + 1]); i += 2
            elif args[i] == "--year" and i + 1 < len(args):
                year_from = int(args[i + 1]); i += 2
            elif args[i] == "--tiers" and i + 1 < len(args):
                tiers = args[i + 1].split(","); i += 2
            else:
                i += 1
        fetch_elite_papers(category_id=cat_id, year_from=year_from, tiers=tiers)

    elif cmd == "elite-cn":
        cat_id = None; year_from = 2022
        args = sys.argv[2:]
        i = 0
        while i < len(args):
            if args[i] == "--cat" and i + 1 < len(args):
                cat_id = int(args[i + 1]); i += 2
            elif args[i] == "--year" and i + 1 < len(args):
                year_from = int(args[i + 1]); i += 2
            else:
                i += 1
        fetch_cn_elite_papers(category_id=cat_id, year_from=year_from)

    elif cmd == "arxiv":
        cat_id = None
        args = sys.argv[2:]
        i = 0
        while i < len(args):
            if args[i] == "--cat" and i + 1 < len(args):
                cat_id = int(args[i + 1]); i += 2
            else:
                i += 1
        fetch_arxiv_papers(category_id=cat_id)

    elif cmd == "fetch-all":
        year_from = 2023
        args = sys.argv[2:]
        i = 0
        while i < len(args):
            if args[i] == "--year" and i + 1 < len(args):
                year_from = int(args[i + 1]); i += 2
            else:
                i += 1
        fetch_all_elite(year_from=year_from)

    elif cmd == "search":
        if len(sys.argv) < 3:
            print("用法: python cli.py search <关键词> [--lang zh|en] [--tier S|A|B]")
            return
        query = " ".join([a for a in sys.argv[2:] if not a.startswith("--")])
        lang = None; min_tier = None
        for i, a in enumerate(sys.argv):
            if a == "--lang" and i + 1 < len(sys.argv):
                lang = sys.argv[i + 1]
            if a == "--tier" and i + 1 < len(sys.argv):
                min_tier = sys.argv[i + 1]
        cols, rows = search_local(query, language=lang, min_tier=min_tier, limit=30)
        print(f"\n🔍 搜索: {query} — 共 {len(rows)} 条\n")
        for r in rows:
            d = dict(zip(cols, r))
            tier_badge = f"[{d.get('journal_tier','?')}]" if d.get('journal_tier') else ""
            print(f"  {tier_badge} [{d['id']}] ({d['year']}) {d['title'][:80]}")
            print(f"      {d['authors'][:60]} | Q={d.get('quality_score',0)} | 引用{d['citation_count']} | {d.get('category_name','')}\n")

    elif cmd == "list":
        cat_filter = sys.argv[2] if len(sys.argv) > 2 else None
        cols, rows = search_local(query=None, category=cat_filter, limit=50)
        print(f"\n📚 共 {len(rows)} 篇\n")
        for r in rows:
            d = dict(zip(cols, r))
            print(f"  [{d['id']}] ({d['year']}) [{d.get('journal_tier','-')}] {d['title'][:80]}")

    elif cmd == "top":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 10
        conn = get_conn()
        rows = conn.execute("""
            SELECT p.id, p.title, p.title_cn, p.journal_tier, p.quality_score,
                   p.authors, p.year, p.citation_count, p.language, c.name
            FROM papers p JOIN categories c ON p.category_id = c.id
            ORDER BY p.journal_tier ASC NULLS LAST, p.quality_score DESC
            LIMIT ?
        """, (n,)).fetchall()
        conn.close()
        print(f"\n🏆 质量评分 TOP {n}\n")
        for i, r in enumerate(rows):
            lang = "🇨🇳" if r[8] == "zh" else "🌐"
            tier = f"[{r[3]}]" if r[3] else ""
            title = r[1]
            if r[2] and r[8] == "zh":
                title = r[2]
            print(f"  {i+1}. {lang} {tier} [{r[0]}] ({r[6]}) Q={r[4]:.1f} 引用{r[7]}")
            print(f"      {title[:75]}")

    elif cmd == "download":
        if len(sys.argv) < 3:
            print("用法: python cli.py download <论文ID>")
            return
        download_pdf(int(sys.argv[2]))

    elif cmd == "download-all":
        conn = get_conn()
        papers = conn.execute(
            "SELECT id FROM papers WHERE is_downloaded=0 AND url IS NOT NULL LIMIT 30"
        ).fetchall()
        conn.close()
        print(f"📥 待下载: {len(papers)} 篇")
        for i, (pid,) in enumerate(papers):
            print(f"\n[{i+1}/{len(papers)}]")
            download_pdf(pid)
            time.sleep(2)

    elif cmd == "stats":
        s = get_stats()
        print(f"\n📊 高端论文数据库统计 (v2)")
        print(f"   总计: {s['total']} 篇 | 已下载: {s['downloaded']} 篇")
        print(f"   中文: {s['cn_count']} 篇 | 英文: {s['en_count']} 篇")
        if s['by_tier']:
            print(f"\n   期刊等级分布:")
            tier_labels = {"S": "顶级(SN/大子刊)", "A": "Q1专业顶刊",
                           "B": "Q2优质期刊", "C": "中文核心", "preprint": "预印本"}
            for tier, count in s['by_tier']:
                label = tier_labels.get(tier, tier)
                bar = "█" * min(count, 30)
                print(f"   [{tier}] {label:20s} {bar} {count}")
        print(f"\n   按分类:")
        for name, count in s['by_category']:
            bar = "█" * min(count, 30)
            print(f"   {name:20s} {bar} {count}")
        print(f"\n   按年份:")
        for year, count in s['by_year']:
            print(f"   {year}: {count} 篇")
        if s['by_source']:
            print(f"\n   按来源:")
            for src, count in s['by_source']:
                print(f"   {src}: {count} 篇")

    else:
        print(f"未知命令: {cmd}")


if __name__ == "__main__":
    cli()
