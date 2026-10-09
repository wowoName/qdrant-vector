"""
文档处理器模块

每种文件类型一个专属处理器类，把"解析 + 切片"封装在一起，
上层无需关心具体格式，只需调用统一接口。

架构图：
    BaseDocumentProcessor  (统一接口)
        ├── ExcelProcessor       (xlsx/xls → TabularChunker)
        ├── CSVProcessor         (csv → TabularChunker)
        ├── PDFProcessor         (pdf → 文本切片器)
        ├── DOCXProcessor        (docx/doc → 文本切片器)
        ├── PPTProcessor         (ppt/pptx → 文本切片器)
        ├── TXTProcessor         (txt → 文本切片器)
        ├── MarkdownProcessor    (md/markdown → 文本切片器)
        ├── HTMLProcessor        (html/htm → 文本切片器)
        ├── ImageProcessor       (图片 → OCR + 文本切片器)
        ├── AudioProcessor       (音频 → ASR + 文本切片器)
        └── VideoProcessor       (视频 → 抽帧OCR + ASR + 文本切片器)

上层调用：
    processor = get_processor(file_type)
    chunks = processor.chunk_file(file_path)   # 全量
    for chunk in processor.chunk_file_stream(file_path):  # 流式
"""
