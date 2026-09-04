-- 高端论文数据库 — 广州市地质调查院（海洋发展促进中心）
-- 基于广州市规划和自然资源局职能体系

CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id INTEGER DEFAULT 0,
    name TEXT NOT NULL,
    keywords TEXT,
    description TEXT,
    FOREIGN KEY (parent_id) REFERENCES categories(id)
);

CREATE TABLE IF NOT EXISTS papers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    title_cn TEXT,
    authors TEXT NOT NULL,
    year INTEGER,
    journal TEXT,
    doi TEXT UNIQUE,
    abstract TEXT,
    abstract_cn TEXT,
    keywords TEXT,
    category_id INTEGER,
    language TEXT DEFAULT 'en',
    source TEXT DEFAULT 'crossref',
    source_id TEXT,
    url TEXT,
    pdf_path TEXT,
    journal_tier TEXT DEFAULT '',
    quality_score REAL DEFAULT 0,
    source_type TEXT DEFAULT '',
    citation_count INTEGER DEFAULT 0,
    is_downloaded INTEGER DEFAULT 0,
    is_read INTEGER DEFAULT 0,
    rating INTEGER DEFAULT 0,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    doi_verified INTEGER DEFAULT 0,
    FOREIGN KEY (category_id) REFERENCES categories(id)
);

CREATE INDEX IF NOT EXISTS idx_papers_category ON papers(category_id);
CREATE INDEX IF NOT EXISTS idx_papers_year ON papers(year);
CREATE INDEX IF NOT EXISTS idx_papers_keywords ON papers(keywords);
CREATE INDEX IF NOT EXISTS idx_papers_title ON papers(title);

CREATE VIRTUAL TABLE IF NOT EXISTS papers_fts USING fts5(
    title, title_cn, authors, abstract, abstract_cn, keywords, journal,
    content='papers', content_rowid='id'
);

-- 触发器：论文增删改时同步FTS索引
CREATE TRIGGER IF NOT EXISTS papers_ai AFTER INSERT ON papers BEGIN
    INSERT INTO papers_fts(rowid, title, title_cn, authors, abstract, abstract_cn, keywords, journal)
    VALUES (new.id, new.title, new.title_cn, new.authors, new.abstract, new.abstract_cn, new.keywords, new.journal);
END;

CREATE TRIGGER IF NOT EXISTS papers_ad AFTER DELETE ON papers BEGIN
    INSERT INTO papers_fts(papers_fts, rowid, title, title_cn, authors, abstract, abstract_cn, keywords, journal)
    VALUES ('delete', old.id, old.title, old.title_cn, old.authors, old.abstract, old.abstract_cn, old.keywords, old.journal);
END;

CREATE TRIGGER IF NOT EXISTS papers_au AFTER UPDATE ON papers BEGIN
    INSERT INTO papers_fts(papers_fts, rowid, title, title_cn, authors, abstract, abstract_cn, keywords, journal)
    VALUES ('delete', old.id, old.title, old.title_cn, old.authors, old.abstract, old.abstract_cn, old.keywords, old.journal);
    INSERT INTO papers_fts(rowid, title, title_cn, authors, abstract, abstract_cn, keywords, journal)
    VALUES (new.id, new.title, new.title_cn, new.authors, new.abstract, new.abstract_cn, new.keywords, new.journal);
END;

-- 搜索历史
CREATE TABLE IF NOT EXISTS search_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query TEXT NOT NULL,
    source TEXT,
    result_count INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 库级元数据（含内容最后更新日期）
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- 初始化分类体系（局+院职能对应）
INSERT OR IGNORE INTO categories (id, parent_id, name, keywords, description) VALUES
(1, 0, '地质灾害监测与预警', 'landslide,debris flow,ground collapse,early warning,geohazard monitoring,risk assessment,地质灾害,滑坡,泥石流,预警,风险评估,InSAR', '地质灾害监测巡查、预报预警、应急抢险'),
(2, 0, '基础地质调查与研究', 'geological survey,regional geology,geophysics,geochemistry,stratigraphy,基础地质,区域地质,地球物理,地球化学,地层', '基础性公益性地调、战略性矿产勘查'),
(3, 0, '矿产资源勘查与管理', 'mineral exploration,resource assessment,mining,reserve estimation,矿产勘查,资源评价,储量管理', '压覆矿产核查、储量动态监管、地质资料汇交'),
(4, 0, '海洋经济与产业发展', 'marine economy,ocean industry,blue economy,marine spatial planning,海洋经济,海洋产业,蓝色经济,用海用岛', '海洋产业促进、运行监测、对外交流'),
(5, 0, '海洋灾害与生态修复', 'marine hazard,coastal erosion,storm surge,ecological restoration,marine ecology,海洋灾害,海岸侵蚀,风暴潮,生态修复,赤潮', '海洋观测预报、生态修复、海洋环境监测'),
(6, 0, '城市地质与地下空间', 'urban geology,underground space,engineering geology,hydrogeology,城市地质,地下空间,工程地质,水文地质,地铁', '城市地下空间调查、地下水监测、工程勘察'),
(7, 0, '国土空间规划', 'spatial planning,land use,urban planning,territorial planning,空间规划,土地利用,城市规划,国土规划', '广州市规划和自然资源局核心职能'),
(8, 0, '自然资源调查监测', 'natural resource survey,remote sensing,land survey,monitoring,自然资源,遥感,调查监测,三调,变更调查', '自然资源调查、动态监测、遥感应用'),
(9, 0, '地质遗迹与科普', 'geological heritage,geopark,fossil,geoscience education,地质遗迹,地质公园,化石,科普,恐龙', '地质遗迹保护、科普宣传、博物馆'),
(10, 0, 'AI与地学交叉应用', 'artificial intelligence,machine learning,deep learning,earth science,big data,人工智能,机器学习,深度学习,大数据,数字孪生', 'AI在地质/海洋中的应用：智能监测、大数据分析');
