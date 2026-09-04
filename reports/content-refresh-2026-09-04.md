# Content refresh — 2026-09-04

Verified against Crossref (`api.crossref.org/works`) and doi.org / China DOI (chndoi). No paper was added unless a DOI resolved. Abstracts are copied from Crossref when present; they are never invented.

## Added (33)

English, Crossref, 2024–2025, geology hazards + mineral resources:

- **地质灾害** (Landslides, Engineering Geology, Geomorphology, GRL): rainfall- and earthquake-induced landslides (Haihe 2023, Luding 2022), InSAR / PINN / UAV methods, global land subsidence.
- **矿产资源** (Economic Geology, Mineralium Deposita, Ore Geology Reviews, Nature Geoscience): Jiama Cu-Mo-Au, Bayan Obo REE/Nb, Huize Ge, Xianghualing Sn, Tethys Himalaya Pb-Zn-Ag-Sb, lithium brines / pegmatites / volcano-sedimentary Li, Qinshui coal-hosted critical metals, North China sandstone uranium.
- **城市地质**: urban underground space flood resilience (Tunnelling and Underground Space Technology).

Chinese / bilingual, DOI-resolved (title matches the resolver, **not** the previous seed label):

| DOI | Resolver title | Category |
|---|---|---|
| 10.18654/1000-0569/2023.05.01 | 锡矿床研究现状及发展趋势 (Acta Petrologica Sinica) | 矿产 |
| 10.18654/1000-0569/2022.05.02 | 从碳源到碳汇: 大陆弧演化… (Acta Petrologica Sinica) | 基础地质 |
| 10.15302/J-SSCAE-2023.03.004 | 海洋科考装备技术发展战略研究 | 海洋经济 |
| 10.19762/j.cnki.dizhixuebao.2021225 | 柴达木盆地别勒滩地区断裂构造对深部卤水分布的控制作用研究 | 矿产 |
| 10.13722/j.cnki.jrme.2023.0054 | 地层塌陷引起土体变形的阵列式三维活动门试验研究 | 地质灾害 |

## Could not verify (Chinese seed file)

All 41 `cn_papers_seed.json` DOIs failed title verification:

- Some DOIs 404 on both Crossref and doi.org (unregistered / invented identifiers).
- Some DOIs resolve to a **different** paper (e.g. `10.18654/1000-0569/2023.05.01` is a tin-deposit review, not the Three Gorges InSAR landslide paper previously stored under that DOI).
- Two seed rows had empty DOIs.

Those 41 bibliographic rows remain in the database as `source_type=cn_seed` **without DOIs**. They are not treated as Crossref-verified. Re-importing the seed file will no longer attach fake DOIs.

## Schema / site

- `meta.last_updated` drives the header stamp (`数据更新于 YYYY-MM-DD`).
- `papers.doi_verified` marks resolver-confirmed DOIs.
- Static export: `python cli.py export-static` → `docs/`.
