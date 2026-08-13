# -*- coding: utf-8 -*-
"""
跨页断表检测 + 合并规则生成（skill 版，参数化）
用法：
  python build_merge_rules.py <PDF路径> [-o merge_rules.json] [--exclude 附表15 附表18 附表21]

输出：
  1. 控制台：跨页断表候选清单（表标题、涉及页、置信度）——展示给用户确认
  2. merge_rules.json：用户确认后的合并组（含页 + 页内表块序号），供 split_pdf.py 使用

算法：表序列分组
- 把所有页的所有表格按阅读顺序（页码+bbox 纵向）排成序列
- 每个表判断「上方是否有表标题」（PyMuPDF 文本）
- 有标题的表 = 新组开头；其后连续的无标题表 = 该组续表
- 组跨 2 页及以上 → 断表候选
"""
import fitz, pdfplumber, re, json, argparse, os

ap = argparse.ArgumentParser()
ap.add_argument("src", nargs="?", default=r"C:\Users\DANTE\Desktop\待拆分.pdf", help="目标 PDF 路径")
ap.add_argument("-o", "--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "merge_rules.json"),
                help="合并规则输出路径")
ap.add_argument("--exclude", nargs="*", default=[], help="用户确认不合并的表标题关键词")
args = ap.parse_args()

SRC = args.src
EXCLUDE_TITLES = args.exclude

TITLE_RE = re.compile(r'^\s*(表|附表)\s*\d+(\.\d+)*[\s\-–—：:]', re.M)

doc = fitz.open(SRC)
page_info = {}
with pdfplumber.open(SRC) as pl:
    for p, page in enumerate(pl.pages):
        tables = []
        for t in page.find_tables():
            try:
                rows = t.extract()
            except Exception:
                rows = None
            tables.append((t.bbox, rows))
        titles = []
        for block in doc[p].get_text("dict").get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                text = "".join(s.get("text", "") for s in line.get("spans", [])).strip()
                if TITLE_RE.match(text):
                    titles.append((line["bbox"][1], text))
        page_info[p] = {"tables": tables, "titles": titles}

def title_near(titles, bbox, tol=95):
    ty0 = bbox[1]
    for t in titles:
        if t[0] <= ty0 + 5 and abs(t[0] - ty0) < tol:
            return t[1]
    return None

def ncols(rows):
    return max((len(r) for r in rows if r), default=0) if rows else 0

def first_cells(rows, k=3):
    out = []
    if not rows:
        return out
    for r in rows[:2]:
        if r:
            c = str(r[0]).strip() if r[0] else ""
            if c:
                out.append(c[:22].replace("\n", " "))
    return out[:k]

# ---- 表序列（含页内序号） ----
all_tables = []
for p in range(doc.page_count):
    for idx, (bbox, rows) in enumerate(page_info[p]["tables"]):
        all_tables.append({
            "page": p + 1, "idx": idx, "bbox": bbox, "rows": rows,
            "title": title_near(page_info[p]["titles"], bbox),
        })

# ---- 分组 ----
groups = []
cur = None
for t in all_tables:
    if t["title"]:
        if cur is not None and len(cur["tables"]) >= 2:
            groups.append(cur)
        cur = {"title": t["title"], "tables": [t]}
    else:
        if cur is None:
            cur = {"title": None, "tables": [t]}
        else:
            cur["tables"].append(t)
if cur is not None and len(cur["tables"]) >= 2:
    groups.append(cur)

# ---- 候选（跨 >=2 页），排除用户指定 ----
candidates = []
for g in groups:
    title = g["title"] or "（无标题开头）"
    if any(ex in title for ex in EXCLUDE_TITLES):
        continue
    pages = sorted({t["page"] for t in g["tables"]})
    if len(pages) < 2:
        continue
    candidates.append({
        "title": title, "pages": pages, "span": len(pages),
        "n_tables": len(g["tables"]),
        "tables": [{"page": t["page"], "idx": t["idx"]} for t in g["tables"]],
        "cols": ncols(g["tables"][0]["rows"]),
        "head_first": first_cells(g["tables"][0]["rows"]),
        "head_cont": first_cells(g["tables"][-1]["rows"]),
    })

candidates.sort(key=lambda c: (c["pages"][0], c["tables"][0]["idx"]))
print(f"=== 疑似跨页断表 {len(candidates)} 处（请用户核对后确认哪些合并）===\n")
for i, c in enumerate(candidates, 1):
    pg = "、".join("第%s页" % x for x in c["pages"])
    print(f"[{i}] 跨 {c['span']} 页（{c['n_tables']} 表块）：{pg}")
    print(f"    标题: {c['title']} | 列数: {c['cols']}")
    print(f"    首表首行: {c['head_first']}")
    print(f"    末表首行: {c['head_cont']}")
    print()

with open(args.out, "w", encoding="utf-8") as f:
    json.dump(candidates, f, ensure_ascii=False, indent=1)
print(f"候选已存 {args.out}")
print("\n>>> 待用户确认后，若需修正跨页范围或排除某些表，")
print("    修改 JSON 中对应组的 pages/tables，或重跑并传 --exclude。")
print("    确认无误后运行 split_pdf.py 生成最终拆分。")
