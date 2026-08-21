# 2026-08-21 · Docling + RapidOCR 解析 Spike（企业升级 Phase 0）

> 产出：`scripts/spike_docling.py`、`output/docling-spike-ocr/`、`output/docling-spike-noocr/`、方案回填 `docs/enterprise-upgrade-plan.md`
> 环境：Docling 2.121.0 + rapidocr-onnxruntime，独立 venv `.venv-docling`（隔离，不污染 food-rag 环境），CPU 推理

## 做了什么

Phase 0 核心验证：用真实年报量化 Docling 的**解析质量**和**单页耗时**，并做 OCR 开/关对照实验。6 份次转换全部成功（3 份 × 两轮），实测数据回填企业方案。

## 用了什么技术

- **Docling 标准管线**：每页跑版面模型（RT-DETR 系 layout model）+ TableFormer 表格结构识别；`PdfPipelineOptions` 控制 `do_ocr` / `do_table_structure`；
- **RapidOCR**（PaddleOCR 模型 ONNX 封装，Docling 原生引擎）：默认只对位图区域 OCR，数字原生页走 PDF 文本层；
- **DocumentStream 字节流转换**：`converter.convert(DocumentStream(name, io.BytesIO(path.read_bytes())))`；
- DoclingDocument 的 `iterate_items()` 元素统计、`export_to_markdown()` / `export_to_dict()`、`prov`（provenance）溯源字段。

## 解决了什么问题（结论）

### 质量结论：全面达标（PASS）

| 检查项 | 实测结果 |
| --- | --- |
| 标题层级 | 完整重建（第X节 -> 一/二 -> （一）-> 1/2），直接可作结构化切块的 `heading_path` |
| 财务表格 | 双层表头（调整前/调整后）、跨页合并资产负债表全部正确重建，数字逐位保真（如 -389,116,496.12 / -153.13%） |
| 溯源定位 | **100% 覆盖**：2443/2443 文本项、360/360 表格均带 `page_no + bbox`（`coord_origin: BOTTOMLEFT`），"元素->页码->高亮矩形"追溯链完整 |
| 文本保真 | 84k 字符：CID 残留 0、替换符 0、私用区字符 174（≈0.2% 字体符号） |

### 吞吐结论：超预算约 10 倍（本次最大发现）

| 文档 | 页数 | 表格 | 关 OCR | 开 OCR |
| --- | --- | --- | --- | --- |
| 美丽生态 2023 | 175 | 360 | 782.0s | 810.4s |
| 东旭光电 2023 | 243 | 422 | 1040.1s | 1055.0s |
| ST红太阳 2023 | 276 | 425 | 1251.1s | 1236.9s |
| **合计** | **694** | **1207** | **3073.2s（4.43s/页）** | **3102.3s（4.47s/页）** |

两个关键判断：

1. **瓶颈不是 OCR，是版面模型 + TableFormer 的 CPU 推理**。694 页开 OCR 仅多 29.1s（0.9%，噪声级，第三份开 OCR 反而更快）。对数字原生文档，`do_ocr=True` 几乎免费且能兜住混入的扫描页 -> **主链路保持 OCR 常开**。
2. 原方案估算"数字原生 0.1-0.5s/页"只对"轻量文本层直取"成立。Docling 标准管线 10 万份 × 30 页 ≈ 300 万页 × 4.4s ≈ 单 worker 150 天 -> **三档解析分流成为硬要求**（轻量文本层 / Docling 标准管线 / OCR 管线），且 10 万份量级必须验证 GPU 推理（预期 5-10 倍）。

## 效果怎么样

- Phase 0 的两个核心问题（质量、吞吐）都有了实测答案，方案里的吞吐预算表全部换成实测值；
- 拿到了完整的结构化产物：markdown 投影、带 prov 的 JSON、stats.csv、summary.json（元素构成：text 3635 / section_header 2038 / table 1204 / list_item 713 / caption 269 / footnote 12 / picture 6），切块策略可以直接按元素类型分流；
- 中文路径 + 字节流的转换方式与未来 MinIO 对象存储"按字节取件"架构天然一致。

## 遇到的问题与该怎么办

| 问题 | 根因 | 应对（通用方法） |
| --- | --- | --- |
| docling-parse 报 "could not load document"，3/3 全失败 | **C++ 原生库在 Windows 下无法加载含中文字符的文件路径**；直接传 BytesIO 又被 pydantic 拒绝（期望 HttpSource） | 用 `DocumentStream(name, io.BytesIO(bytes))` 包装后成功。**方法：原生扩展库的路径问题，先怀疑编码/平台差异，字节流包装是最通用的规避手段；顺带发现这与对象存储架构天然对齐** |
| 单份 175 页文档转换 804s，直觉以为 OCR 太慢 | 没有分环节计时，靠猜 | 做了 OCR 开/关对照实验，定位真凶是版面+表格模型。**方法：性能问题永远做对照实验拆变量，不靠直觉** |
| spike 收尾时 CSV 写入崩溃 `dict contains fields not in fieldnames: 'labels'` | `{**row, **row.pop('labels', {})}` 里 `**row` 先解包（含 labels 键），pop 后才合并，labels 键残留 | 先 pop 再解包；还连带发现第二个 bug：CSV 循环原地 pop 会让后续 summary 的 label_totals 变空 -> 改成拷贝后操作。**方法：一行里既解包又改原对象的写法是 bug 温床；数据聚合脚本要保证"中间产物不破坏原始数据"** |
| 转换太慢导致每轮实验 45 分钟 | 单 worker 串行 | 接受（spike 要的是干净的单 worker 数据）；正式集成时用多 worker / GPU。**方法：基准实验要干净的串行数据，工程吞吐靠并行，两者分开测** |

## 遗留 / 下一步

- [ ] 扫描件样张（现语料全是数字原生，需合成退化样本或补真实扫描件验证 OCR 主链路质量）；
- [ ] `do_table_structure=False` 对照，量化 TableFormer 在 4.4s/页里的占比；
- [ ] GPU 推理验证（CUDA torch + docling AcceleratorOptions）；
- [ ] PaddleOCR-VL 对比评测；
- [ ] 框架接入：Docling 作为实验性解析器并入现有入库链路（与 PyMuPDF 管线并行对比）；环境决策：docling 装进 food-rag 主环境 vs 独立 venv 子进程桥接（倾向前者）。

## 复习要点

- Docling 的成本结构：版面模型 + TableFormer（每页必跑） >> OCR（数字原生文档上趋近于 0）；
- `DocumentStream(name, BytesIO)` 是 Windows 中文路径的解，也是对象存储架构的正确姿势；
- DoclingDocument 的 `prov`（page_no + bbox）就是可追溯引用的数据基础，与现有前端的页码高亮方案直接对接；
- spike 的价值公式：**用 3 份样本 × 2 轮对照（约 100 分钟机器时间），修正了 10 倍量级的架构估算**。
