---
name: pdf-ragflow-split
description: 将 PDF 文档拆分为 RAGFlow 可高效识别的三类 Markdown（text.md 正文 + tables.md 表格 + images/ 图片）。流程含：跨页断表自动检测并生成清单交用户确认、按确认结果合并跨页表格、自动校正图片方向、生成可直接导入 RAGFlow 的上传文件夹与配置注意事项。当用户要求把 PDF 拆分/转换为 RAGFlow 友好的 md、执行「文档拆分→向量化前置」流程、或重建该工作流时使用。
agent_created: true
---

# PDF → RAGFlow 三类拆分

将 PDF 拆成 text.md（正文）、tables.md（表格）、images/（图片）三件套，保证跨文件引用一致、可整包导入 RAGFlow。RAGFlow 对 MD 兼容性最佳，目标是让它「零歧义」识别。

## 前置条件

- 托管 Python venv（Windows）：`binaries/python/envs/default/Scripts/python.exe`
- 依赖：`pymupdf`、`pdfplumber`、`Pillow`
- 安装命令：`<venv-python> -m pip install pymupdf pdfplumber Pillow`
- 待拆分 PDF 的绝对路径

## 工作流（三步，务必按序执行）

### 步骤一：检测跨页断表 + 用户确认

1. 运行 `scripts/build_merge_rules.py <PDF路径> [-o merge_rules.json] [--exclude 不合并的表标题关键词...]`
2. 将输出的**候选清单**（表标题、涉及页、置信度）展示给用户确认：
   - 用户确认哪些合并、哪些不合并
   - 用户可能修正跨页范围（自动检测可能过度延伸，以用户核对 PDF 原文为准）
3. 用户确认后，用 `--exclude` 重跑或直接手工编辑 merge_rules.json（删除/修正不需要的组）
4. **保留 merge_rules.json**（后续 split_pdf.py 依赖它）

### 步骤二：拆分生成三件套

1. 运行 `scripts/split_pdf.py <PDF路径> -o <输出目录> -m merge_rules.json [-t 文档标题]`
2. 输出目录下生成：
   - `text.md`：正文，按页 `## 第 N 页` 分段；图片相对引用 `images/xxx.png`；表格区域用引用标识关联 tables.md
   - `tables.md`：全部表格，跨页断表已合并（保留起始页标题/锚点，来源标注续表页），锚点回链 text.md
   - `images/`：图片按 xref 去重，方向已自动校正

### 步骤三：生成 RAGFlow 上传文件夹 + 配置说明

1. 创建 `ragflow_upload/`，复制 text.md、tables.md、images/（**保持相对目录结构**）
2. 按 `references/ragflow-config.md` 向用户说明 RAGFlow 知识库配置要点

## 关键避坑（均已固化为脚本逻辑）

1. **取图片 xref**：用 `page.get_images(full=True)` 拿 xref + `page.get_image_rects(xref)` 拿位置。⚠️ 不要用 `get_image_info()` 的字段取 xref——它返回的是 `number` 而非 xref，会导致图片全丢。
2. **图片上下颠倒**：PDF 中图像 transform 矩阵第 4 位（d）<0 表示垂直翻转嵌入，直接提取的位图是倒的。用 `page.get_image_info(xrefs=[xref])` 检查 transform，d<0 时用 Pillow `FLIP_TOP_BOTTOM` 翻转。
3. **跨页断表检测**：pdfplumber 可能把同页多个表合并为一个表块，且列数相同的续表会跨页误链。正确算法：把所有表块排成序列，按「表上方是否有标题」分组（有标题=新组开头，其后无标题表=续表），组跨 2 页及以上为候选。用户修正的跨页范围**优先于**自动检测。
4. **合并表去重表头**：续表块首行若与起始表表头归一化后相同，跳过该行。
5. **正文跳过表格文字**：用「块中心是否落入表格 bbox」判定，避免与 tables.md 重复。
6. **markdown 表格生成**：过滤全空行、单元格 `|` 转义为 `\|`、首行后加 `|---|` 分隔。

## 校验（生成后必做）

- 表标题数 = 锚点数 = text.md 引用数（双向一致）
- 无重复「页码-表号」组合
- 表格行格式零异常（`|` 开头、行尾 `|`、`---` 分隔）
- text.md 引用的图片文件都存在于 images/
- 校验通过后再交付或上传

## 交付

- 展示 text.md、tables.md
- 说明 ragflow_upload/ 路径与 RAGFlow 配置要点（见 references/ragflow-config.md）
