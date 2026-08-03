"""批量翻译英文标题 → 中文，使用 MyMemory API (urllib直连)"""
import urllib.request, urllib.parse, json, time, sys, sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "db" / "papers.db"

def translate(text, source='en-GB', target='zh-CN'):
    email = 'research@gz.gov.cn'
    url = 'https://api.mymemory.translated.net/get'
    params = urllib.parse.urlencode({'q': text, 'langpair': f'{source}|{target}', 'de': email})
    full_url = f'{url}?{params}'
    req = urllib.request.Request(full_url, headers={'User-Agent': 'PaperDB/1.0'})
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode())
            # Check quota
            if data.get('quotaFinished'):
                return '[QUOTA]'
            result = data.get('responseData', {}).get('translatedText', '')
            if result and result != text and len(result) > 2:
                return result
    except Exception as e:
        pass
    return None

def main():
    conn = sqlite3.connect(str(DB_PATH))
    
    # 找未翻译的英文论文
    rows = conn.execute("""
        SELECT id, title FROM papers
        WHERE language='en' AND (title_cn IS NULL OR title_cn='' OR title_cn=title)
        ORDER BY id
    """).fetchall()
    
    if not rows:
        print("✅ 全部已翻译")
        conn.close()
        return
    
    total = len(rows)
    print(f"📝 待翻译: {total} 篇\n")
    
    ok = fail = chars = 0
    start = time.time()
    
    for i, (pid, title) in enumerate(rows):
        title_clean = title.strip()[:400]
        if len(title_clean) < 5:
            continue
        
        result = translate(title_clean)
        if result == '[QUOTA]':
            print(f"\n  ⚠️  MyMemory配额用完, 已翻译 {ok} 篇, 停止")
            break
        elif result:
            conn.execute("UPDATE papers SET title_cn=? WHERE id=?", (result, pid))
            ok += 1
            chars += len(title_clean)
        else:
            fail += 1
        
        # 每10篇输出+提交一次
        if (i+1) % 10 == 0:
            conn.commit()
            elapsed = time.time() - start
            print(f"  [{i+1}/{total}] ✅{ok} ❌{fail} | {chars}字符 | {elapsed:.0f}s | 速度: {ok/elapsed*60:.1f}篇/分")
        
        time.sleep(0.5)
        
        # 每50篇暂停一下
        if ok > 0 and ok % 50 == 0:
            print(f"  ⏸️  已翻译 {ok} 篇, 暂停8秒...")
            time.sleep(8)
    
    conn.commit()
    # 重建FTS
    conn.execute("INSERT INTO papers_fts(papers_fts) VALUES('rebuild')")
    conn.close()
    
    elapsed = time.time() - start
    print(f"\n✅ 完成: {ok}篇翻译, {fail}篇失败, {chars}字符, 耗时 {elapsed:.0f}s\n")

if __name__ == "__main__":
    main()
