# -*- coding: utf-8 -*-
"""
PDF -> RAGFlow 友好三类拆分（skill 版，参数化）：
  text.md     : 正文文字 + 图片相对引用 + 表格关联标识
  tables.md   : 全部表格合并（含跨页断表合并）+ 锚点回链 text.md
  images/     : 提取的图片（按 xref 去重，自动校正垂直翻转）

用法：
  python split_pdf.py <PDF路径> [-o 输出目录] [-m merge_rules.json] [-t 文档标题]
"""
import fitz, pdfplumber, os, re, json, argparse

ap = argparse.ArgumentParser()
ap.add_argument("src", nargs="?", default=r"C:\Users\DANTE\Desktop\待拆分.pdf", help="目标 PDF 路径")
ap.add_argument("-o", "--out", default=r"C:\Users\DANTE\WorkBuddy\文档拆分\output", help="输出目录（生成 text.md / tables.md / images/）")
ap.add_argument("-m", "--merge-rules", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "merge_rules.json"),
                help="跨页合并规则 JSON（由 build_merge_rules.py 生成）")
ap.add_argument("-t", "--title", default=None, help="文档标题（默认取 PDF 文件名）")
args = ap.parse_args()

SRC = args.src
OUT = args.out
IMG_DIR = os.path.join(OUT, "images")
os.makedirs(IMG_DIR, exist_ok=True)

DOC_TITLE = args.title or os.path.splitext(os.path.basename(SRC))[0]

# ---------- 0. 合并规则（跨页断表合并，由 build_merge_rules.py 生成） ----------
MERGE_RULES_PATH = args.merge_rules
try:
    with open(MERGE_RULES_PATH, encoding="utf-8") as f:
        MERGE_GROUPS = json.load(f)
except Exception:
    MERGE_GROUPS = []
skip_blocks = set()     # (page_0idx, idx) 被合并掉的续表块
merge_starts = {}       # (page_0idx, idx) -> group（起始表块）
merge_anchor = {}       # (page_0idx, idx) -> (p0, i0) 续表块指向的起始表块
for g in MERGE_GROUPS:
    tabs = g.get("tables", [])
    if not tabs:
        continue
    p0, i0 = tabs[0]["page"] - 1, tabs[0]["idx"]
    merge_starts[(p0, i0)] = g
    for t in tabs[1:]:
        k = (t["page"] - 1, t["idx"])
        skip_blocks.add(k)
        merge_anchor[k] = (p0, i0)
print(f">>> 合并规则：{len(MERGE_GROUPS)} 组，被合并续表块 {len(skip_blocks)} 个")

# ---------- 1. 用 pdfplumber 提取表格（bbox + 数据） ----------
print(">>> 提取表格 ...")
tables_by_page = {}      # page_idx -> [ (bbox, rows), ... ]
with pdfplumber.open(SRC) as pl:
    for i, page in enumerate(pl.pages):
        found = page.find_tables()
        if not found:
            continue
        lst = []
        for t in found:
            bbox = t.bbox  # (x0,y0,x1,y1) 左上原点，点单位
            try:
                rows = t.extract()
            except Exception:
                rows = None
            lst.append((bbox, rows))
        tables_by_page[i] = lst

# ---------- 2. 用 PyMuPDF 提取文本块 / 图片，并与表格位置对齐 ----------
doc = fitz.open(SRC)
total_pages = doc.page_count

# 图片去重：xref -> 文件名
img_xref_map = {}
img_counter = 0

def img_filename(xref, ext):
    global img_counter
    if xref in img_xref_map:
        return img_xref_map[xref]
    img_counter += 1
    fn = f"img_{xref}.{ext}"
    img_xref_map[xref] = fn
    return fn

def center_inside(bbox, tbl_boxes):
    cx = (bbox[0] + bbox[2]) / 2
    cy = (bbox[1] + bbox[3]) / 2
    for tb in tbl_boxes:
        if tb[0] <= cx <= tb[2] and tb[1] <= cy <= tb[3]:
            return True
    return False

# 标题检测：数字编号 / 中文编号 开头的单行短文本
HEAD_RE = re.compile(r'^\s*(\d+(\.\d+)*|[一二三四五六七八九十]+、|\(?[一二三四五六七八九十]+\)?)\s*[\u4e00-\u9fa5A-Za-z]')

def block_to_text(block):
    lines = []
    for line in block.get("lines", []):
        spans = line.get("spans", [])
        s = "".join(sp.get("text", "") for sp in spans).strip()
        if s:
            lines.append(s)
    return "\n".join(lines).strip()

text_lines = []
text_lines.append("---")
text_lines.append(f"title: {DOC_TITLE}")
text_lines.append(f"source_file: {os.path.basename(SRC)}")
text_lines.append(f"pages: {total_pages}")
text_lines.append(f"tables_total: {sum(len(v) for v in tables_by_page.values()) - len(skip_blocks)}")
text_lines.append(f"images_total: {len(img_xref_map)}")
text_lines.append("related: tables.md（全部表格）, images/（全部图片）")
text_lines.append("---")
text_lines.append("")
text_lines.append(f"# {DOC_TITLE} — 正文")
text_lines.append("")
text_lines.append("> 本文件为拆分后的**正文文字**部分。文档中的表格已移至 `tables.md`，"
                  "图片保存在 `images/` 并以相对路径引用。表格在正文中以引用标识标注其位置与对应表号。")
text_lines.append("")

total_tables_written = 0
total_imgs_written = 0
skipped_pages = []  # 既无文本也无图片的页（可能为扫描页）

for p in range(total_pages):
    page = doc[p]
    tbl_boxes = [b[0] for b in tables_by_page.get(p, [])]

    # 文本块
    text_blocks = []
    for b in page.get_text("dict").get("blocks", []):
        if b.get("type") == 0:
            text_blocks.append((b["bbox"], block_to_text(b)))

    # 图片块（用 get_images 拿 xref，get_image_rects 拿位置，比 get_image_info 可靠）
    img_infos = []
    for xref in set(x[0] for x in page.get_images(full=True)):
        rects = page.get_image_rects(xref)
        if rects:
            for r in rects:
                img_infos.append((tuple(r), xref))
        else:
            img_infos.append(((0.0, 0.0, 0.0, 0.0), xref))  # 无位置信息，置顶

    # 表格位置（用于正文插入引用）
    tbl_items = []
    for ti, (bbox, rows) in enumerate(tables_by_page.get(p, [])):
        tbl_items.append((bbox, ti))

    # 合并并按阅读顺序（上->下, 左->右）排序
    items = []
    for bbox, txt in text_blocks:
        if center_inside(bbox, tbl_boxes):
            continue  # 属于表格区域的文字，跳过（已在 tables.md）
        items.append((bbox[1], bbox[0], "text", txt))
    for bbox, xref in img_infos:
        items.append((bbox[1], bbox[0], "img", xref))
    for bbox, ti in tbl_items:
        items.append((bbox[1], bbox[0], "tbl", ti))
    items.sort(key=lambda x: (x[0], x[1]))

    if not items:
        skipped_pages.append(p + 1)
        continue

    text_lines.append(f"## 第 {p+1} 页  {{#page-{p+1}}}")
    text_lines.append("")
    for y0, x0, kind, payload in items:
        if kind == "text":
            txt = payload
            if not txt:
                continue
            # 标题判定
            if "\n" not in txt and len(txt) <= 50 and HEAD_RE.match(txt):
                text_lines.append(f"### {txt}")
            else:
                text_lines.append(txt)
            text_lines.append("")
        elif kind == "img":
            xref = payload
            im = doc.extract_image(xref)
            ext = im["ext"]
            fn = img_filename(xref, ext)
            fpath = os.path.join(IMG_DIR, fn)
            if not os.path.exists(fpath):
                with open(fpath, "wb") as f:
                    f.write(im["image"])
                # 垂直翻转校正：PDF 中图像 transform 的 d<0 表示被垂直翻转嵌入，
                # 直接提取的位图会上下颠倒，需用 Pillow 翻转后覆盖保存。
                try:
                    infos = page.get_image_info(xrefs=[xref])
                    need_flip = any((d.get("transform") or [0] * 6)[3] < 0 for d in infos)
                    if need_flip:
                        from PIL import Image as PILImage
                        pil = PILImage.open(fpath)
                        pil = pil.transpose(PILImage.FLIP_TOP_BOTTOM)
                        pil.save(fpath)
                except Exception:
                    pass
                total_imgs_written += 1
            text_lines.append(f"![图 第{p+1}页](images/{fn})")
            text_lines.append("")
        elif kind == "tbl":
            ti = payload
            if (p, ti) in skip_blocks:
                p0, i0 = merge_anchor[(p, ti)]
                text_lines.append(
                    f"> 📊 **表格 第{p0+1}页-表{i0+1}（续表，已并入）**：详见 "
                    f"[tables.md](tables.md#tbl-p{p0}-t{i0})（对应 text.md 第{p0+1}页）")
            else:
                text_lines.append(
                    f"> 📊 **表格 第{p+1}页-表{ti+1}**：详见 "
                    f"[tables.md](tables.md#tbl-p{p}-t{ti})（对应 text.md 第{p+1}页）")
            text_lines.append("")

# ---------- 3. 写 text.md ----------
text_lines[5] = f"images_total: {len(img_xref_map)}"
text_md = "\n".join(text_lines)
with open(os.path.join(OUT, "text.md"), "w", encoding="utf-8") as f:
    f.write(text_md)

# ---------- 4. 写 tables.md ----------
tlines = []
tlines.append("---")
tlines.append(f"title: {DOC_TITLE} — 表格汇总")
tlines.append(f"source_file: {os.path.basename(SRC)}")
tlines.append(f"tables_total: {sum(len(v) for v in tables_by_page.values()) - len(skip_blocks)}")
tlines.append("related: text.md（正文与图片）, images/（图片）")
tlines.append("---")
tlines.append("")
tlines.append(f"# {DOC_TITLE} — 表格汇总")
tlines.append("")
tlines.append("> 本文件汇总原文档**全部表格**，按「页码-表序号」排序。每个表格均标注其在 "
              "`text.md` 中的对应位置，便于溯源与 RAGFlow 关联检索。")
tlines.append("")

def rows_to_md(rows):
    if not rows:
        return None
    # 过滤全空行
    clean = []
    for r in rows:
        if r is None:
            continue
        cells = [("" if c is None else str(c).replace("\n", " ").strip()) for c in r]
        if any(cells):
            clean.append(cells)
    if not clean:
        return None
    ncol = max(len(r) for r in clean)
    def esc(c):
        return c.replace("|", "\\|")
    out = []
    for ri, r in enumerate(clean):
        while len(r) < ncol:
            r = r + [""]
        out.append("| " + " | ".join(esc(c) for c in r) + " |")
        if ri == 0:
            out.append("| " + " | ".join("---" for _ in range(ncol)) + " |")
    return "\n".join(out)

def norm_cell(c):
    return re.sub(r"\s+", "", str(c)) if c is not None else ""

for p in sorted(tables_by_page.keys()):
    for ti, (bbox, rows) in enumerate(tables_by_page[p]):
        # 被合并的续表块：跳过
        if (p, ti) in skip_blocks:
            continue
        # 起始表块：生成合并表
        if (p, ti) in merge_starts:
            g = merge_starts[(p, ti)]
            other_rows = []
            other_pages = []
            for t in g["tables"][1:]:
                pr, ir = t["page"] - 1, t["idx"]
                if pr in tables_by_page and ir < len(tables_by_page[pr]):
                    other_rows.append(tables_by_page[pr][ir][1])
                    other_pages.append(pr + 1)
            # 合并行：续表块首行若与起始表头相同则跳过（去重表头）
            header0 = rows[0] if rows and rows[0] else None
            hnorm = norm_cell(header0[0]) if header0 else ""
            merged = [r for r in rows]
            for o in other_rows:
                if not o:
                    continue
                o2 = list(o)
                if o2 and hnorm and norm_cell(o2[0][0]) == hnorm:
                    o2 = o2[1:]
                merged.extend(o2)
            tlines.append(f"### 表格 第{p+1}页-表{ti+1}  {{#tbl-p{p}-t{ti}}}")
            tlines.append("")
            src = f"> 来源：text.md 第 {p+1} 页（[返回](text.md#page-{p+1})）"
            if other_pages:
                src += " · 续表：第 " + "、".join(str(x) for x in other_pages) + " 页"
            tlines.append(src)
            tlines.append("")
            md = rows_to_md(merged)
            if md:
                tlines.append(md)
            else:
                tlines.append("> （该表格未能提取到文本数据，可能为图片型表格，请参见原文档或 images/ 目录）")
            tlines.append("")
            continue
        # 普通表块
        tlines.append(f"### 表格 第{p+1}页-表{ti+1}  {{#tbl-p{p}-t{ti}}}")
        tlines.append("")
        tlines.append(f"> 来源：text.md 第 {p+1} 页（[返回](text.md#page-{p+1})）")
        tlines.append("")
        md = rows_to_md(rows)
        if md:
            tlines.append(md)
        else:
            tlines.append("> （该表格未能提取到文本数据，可能为图片型表格，请参见原文档或 images/ 目录）")
        tlines.append("")

tables_md = "\n".join(tlines)
with open(os.path.join(OUT, "tables.md"), "w", encoding="utf-8") as f:
    f.write(tables_md)

print("=== 完成 ===")
print("pages:", total_pages)
print("tables:", sum(len(v) for v in tables_by_page.values()))
print("images extracted (unique xref):", len(img_xref_map))
print("text.md bytes:", len(text_md))
print("tables.md bytes:", len(tables_md))
print("skipped empty pages:", skipped_pages if skipped_pages else "none")
print("OUT dir:", OUT)
print("image files:", sorted(os.listdir(IMG_DIR)))
