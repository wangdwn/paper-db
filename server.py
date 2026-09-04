"""
高端论文数据库 — Web管理界面
"""
import sqlite3
import json
import os
import sys
from pathlib import Path
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "db" / "papers.db"
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"
PORT = 8765


def get_conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


class APIHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(BASE_DIR), **kwargs)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if path == "/api/stats":
            self.send_json_response(self._get_stats())
        elif path == "/api/papers":
            self.send_json_response(self._get_papers(params))
        elif path == "/api/categories":
            self.send_json_response(self._get_categories())
        elif path == "/api/search":
            self.send_json_response(self._search_papers(params))
        elif path == "/api/paper":
            self.send_json_response(self._get_paper(params))
        elif path == "/api/export":
            self._export_papers(params)
        elif path == "/" or path == "/index.html":
            self.serve_html("index.html")
        elif path.startswith("/static/"):
            super().do_GET()
        else:
            self.send_error(404)

    def do_POST(self):
        parsed = urlparse(self.path)
        content_length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(content_length)) if content_length > 0 else {}

        if parsed.path == "/api/paper/note":
            self.send_json_response(self._update_note(body))
        elif parsed.path == "/api/paper/rate":
            self.send_json_response(self._update_rating(body))
        elif parsed.path == "/api/fetch":
            # 触发后台检索
            self.send_json_response({"status": "ok", "message": "请在CLI中执行 python cli.py fetch 命令"})
        else:
            self.send_error(404)

    def send_json_response(self, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def serve_html(self, filename):
        filepath = TEMPLATES_DIR / filename
        if filepath.exists():
            with open(filepath, "r", encoding="utf-8") as f:
                html = f.read()
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)

    def _get_stats(self):
        conn = get_conn()
        total = conn.execute("SELECT COUNT(*) as c FROM papers").fetchone()["c"]
        downloaded = conn.execute("SELECT COUNT(*) as c FROM papers WHERE is_downloaded=1").fetchone()["c"]
        cn_count = conn.execute("SELECT COUNT(*) as c FROM papers WHERE language='zh'").fetchone()["c"]
        en_count = conn.execute("SELECT COUNT(*) as c FROM papers WHERE language='en' OR language IS NULL").fetchone()["c"]
        by_cat = [dict(r) for r in conn.execute("""
            SELECT c.id, c.name, COUNT(p.id) as count
            FROM categories c LEFT JOIN papers p ON c.id = p.category_id
            GROUP BY c.id ORDER BY c.id
        """).fetchall()]
        by_year = [dict(r) for r in conn.execute("""
            SELECT year, COUNT(*) as count FROM papers
            WHERE year IS NOT NULL
            GROUP BY year ORDER BY year DESC LIMIT 10
        """).fetchall()]
        # 期刊等级分布
        by_tier = [dict(r) for r in conn.execute("""
            SELECT COALESCE(journal_tier, '?') as tier, COUNT(*) as count
            FROM papers GROUP BY journal_tier ORDER BY
            CASE journal_tier WHEN 'S' THEN 1 WHEN 'A' THEN 2 WHEN 'B' THEN 3 WHEN 'C' THEN 4 ELSE 5 END
        """).fetchall()]
        # 数据源分布
        by_source = [dict(r) for r in conn.execute("""
            SELECT COALESCE(source_type, source) as src, COUNT(*) as count
            FROM papers GROUP BY source_type, source ORDER BY count DESC
        """).fetchall()]
        top = [dict(r) for r in conn.execute("""
            SELECT id, title, title_cn, authors, citation_count, language, journal_tier
            FROM papers ORDER BY CASE WHEN language='zh' THEN 0 ELSE 1 END, citation_count DESC LIMIT 5
        """).fetchall()]
        last_updated = None
        try:
            row = conn.execute("SELECT value FROM meta WHERE key='last_updated'").fetchone()
            last_updated = row["value"] if row else None
        except Exception:
            last_updated = None
        with_title_cn = conn.execute(
            "SELECT COUNT(*) as c FROM papers WHERE title_cn IS NOT NULL AND title_cn!=''"
        ).fetchone()["c"]
        conn.close()
        return {"total": total, "downloaded": downloaded, "cn_count": cn_count, "en_count": en_count,
                "by_category": by_cat, "by_year": by_year, "by_tier": by_tier, "by_source": by_source, "top": top,
                "last_updated": last_updated, "with_title_cn": with_title_cn}

    def _get_papers(self, params):
        conn = get_conn()
        cat_id = params.get("cat", [None])[0]
        page = int(params.get("page", [1])[0])
        per_page = min(int(params.get("per_page", [20])[0]), 100)
        offset = (page - 1) * per_page

        where = ""
        args = []
        if cat_id:
            where = "WHERE p.category_id = ?"
            args.append(int(cat_id))

        total = conn.execute(f"SELECT COUNT(*) FROM papers p {where}", args).fetchone()[0]
        rows = [dict(r) for r in conn.execute(f"""
            SELECT p.*, c.name as category_name
            FROM papers p LEFT JOIN categories c ON p.category_id = c.id
            {where} ORDER BY CASE WHEN p.language='zh' THEN 0 ELSE 1 END, p.citation_count DESC LIMIT ? OFFSET ?
        """, args + [per_page, offset]).fetchall()]
        conn.close()
        return {"papers": rows, "total": total, "page": page, "per_page": per_page, "pages": max(1, (total + per_page - 1) // per_page)}

    def _get_categories(self):
        conn = get_conn()
        rows = [dict(r) for r in conn.execute("""
            SELECT c.*, COUNT(p.id) as paper_count
            FROM categories c LEFT JOIN papers p ON c.id = p.category_id
            GROUP BY c.id ORDER BY c.id
        """).fetchall()]
        conn.close()
        return rows

    def _search_papers(self, params):
        q = params.get("q", [""])[0]
        if not q:
            return {"papers": [], "total": 0}
        conn = get_conn()
        try:
            rows = [dict(r) for r in conn.execute("""
                SELECT p.*, c.name as category_name
                FROM papers p
                JOIN papers_fts fts ON p.id = fts.rowid
                LEFT JOIN categories c ON p.category_id = c.id
                WHERE papers_fts MATCH ?
                ORDER BY CASE WHEN p.language='zh' THEN 0 ELSE 1 END, rank LIMIT 30
            """, (q,)).fetchall()]
        except:
            rows = []
        conn.close()
        return {"papers": rows, "total": len(rows)}

    def _get_paper(self, params):
        pid = params.get("id", [None])[0]
        if not pid:
            return {"error": "缺少id参数"}
        conn = get_conn()
        row = conn.execute("""
            SELECT p.*, c.name as category_name
            FROM papers p LEFT JOIN categories c ON p.category_id = c.id
            WHERE p.id = ?
        """, (int(pid),)).fetchone()
        conn.close()
        return dict(row) if row else {"error": "论文不存在"}

    def _update_note(self, body):
        conn = get_conn()
        conn.execute("UPDATE papers SET notes=? WHERE id=?", (body.get("notes", ""), body.get("id")))
        conn.commit()
        conn.close()
        return {"status": "ok"}

    def _update_rating(self, body):
        conn = get_conn()
        conn.execute("UPDATE papers SET rating=? WHERE id=?", (body.get("rating", 0), body.get("id")))
        conn.commit()
        conn.close()
        return {"status": "ok"}

    def _export_papers(self, params):
        # 导出为JSON
        conn = get_conn()
        cat_id = params.get("cat", [None])[0]
        if cat_id:
            rows = [dict(r) for r in conn.execute("""
                SELECT p.*, c.name as category_name
                FROM papers p JOIN categories c ON p.category_id = c.id
                WHERE p.category_id = ? ORDER BY p.citation_count DESC
            """, (int(cat_id),)).fetchall()]
        else:
            rows = [dict(r) for r in conn.execute("""
                SELECT p.*, c.name as category_name
                FROM papers p JOIN categories c ON p.category_id = c.id
                ORDER BY p.citation_count DESC
            """).fetchall()]
        conn.close()

        body = json.dumps(rows, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Disposition", "attachment; filename=papers_export.json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # 静默


def main():
    # 确保数据库存在
    if not DB_PATH.exists():
        from cli import init_db
        init_db()
        print("✅ 数据库已初始化")

    server = HTTPServer(("0.0.0.0", PORT), APIHandler)
    print(f"""
╔══════════════════════════════════════════════╗
║      📚 高端论文数据库 v2 — 双语版             ║
║                                              ║
║  打开浏览器访问: http://localhost:{PORT}       ║
║                                              ║
║  功能: 分类浏览 | 全文检索 | 中英双语         ║
║  期刊: S/A/B/C 四级分级 | 质量评分             ║
║  数据: Crossref定向 | MyMemory翻译 | 知网补充  ║
╚══════════════════════════════════════════════╝
    """)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 已关闭")
        server.server_close()


if __name__ == "__main__":
    main()
