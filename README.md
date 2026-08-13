# pdf-ragflow-split
将 PDF 文档拆分为 RAGFlow 可高效识别的三类 Markdown（text.md 正文 + tables.md 表格 + images/ 图片）。流程含：跨页断表自动检测并生成清单交用户确认、按确认结果合并跨页表格、自动校正图片方向、生成可直接导入 RAGFlow 的上传文件夹与配置注意事项。当用户要求把 PDF 拆分/转换为 RAGFlow 友好的 md、执行「文档拆分→向量化前置」流程、或重建该工作流时使用。
