# 迁移指南：把工作台搬到有 RTX 5060 的 Windows 机器

> 目标：新机器成为"解析工作站"（GPU 加速入库 + 全量重灌）；本机可继续当日常开发机。
> 总体思路：**代码和数据搬过去，环境在新机器重建**——因为新机器本来就要装 CUDA 版 torch，直接拷贝旧环境（CPU 版 torch + 旧路径）反而会打架。

## 一、要搬什么

| 内容 | 位置 | 体量 | 搬法 |
| --- | --- | --- | --- |
| 代码仓库 | 整个项目目录（含 `.git`） | 几 MB | U 盘/局域网拷贝（或 push 到私有远端再 clone） |
| 年报语料 | `corpus/raw/` | 460 MB | 随仓库拷贝 |
| 业务数据 | `data/`（SQLite 库 + uploads 产物） | 88 MB | 随仓库拷贝 |
| API 密钥 | `.env`（DASHSCOPE_API_KEY） | <1 KB | 随仓库拷贝（git 故意忽略它，别忘了） |
| Python 环境 | `.conda/envs/food-rag/` | ~10 GB | **不搬**，新机器按第三节重建 |
| Milvus 向量数据 | Docker 卷 | ~几百 MB | **不搬**，重灌重建（见第二节） |
| 前端依赖 | `frontend/node_modules/` | ~几百 MB | **不搬**，`npm ci` 重建 |

本机打包命令（Git Bash，排除不搬的部分）：

```bash
cd /e/A_project
tar --exclude='RAG_knowledge/node_modules' \
    --exclude='RAG_knowledge/.conda' \
    --exclude='RAG_knowledge/frontend/node_modules' \
    --exclude='RAG_knowledge/frontend/dist' \
    --exclude='RAG_knowledge/.pytest_tmp' \
    -czf RAG_knowledge_migrate.tar.gz RAG_knowledge
# 产物约 550MB，U 盘或scp到新机器后解压
```

## 二、Milvus 向量数据为什么不搬（数据集策略）

向量库（food_safety_chunks_v3，13,000+ 块的 embedding）**直接丢弃，到新机器重新上传生成**：

1. 计划本来就是全量重灌——断数字修复、小表不拆分、表格实体前缀三个确诊问题都要重灌才能根治，GPU 到位后正好一次做完；
2. 重灌的原料（corpus/raw 的 100 份 PDF）必须随机器走，原料在，向量随时能再生（embedding 走 DashScope API，跟本机无关）；
3. Milvus 有官方 backup 工具可以做卷迁移，但为一份"注定要重建的一次性数据"引入额外工具链不值。

**连带影响与处理**：SQLite 里的旧文档记录指向 Milvus 里已不存在的向量——旧文档在界面上能看到、但检索不到。两种处理：

- **推荐**：照常拷贝 `data/`（保留全部会话历史、模型配置、检索配置），到新机器后把旧文档逐个删除（界面操作即可），然后用 GPU 管线重新上传全量语料；
- 或者：新机器的 `data/` 从零开始（历史全部丢弃，系统全新）。

## 三、新机器搭建步骤

### 1. 基础软件

- Git、Node.js（≥20）、Docker Desktop、Miniconda
- NVIDIA 驱动（随显卡装好即可）

### 2. 解压项目并启动 Milvus

```bash
tar -xzf RAG_knowledge_migrate.tar.gz
cd RAG_knowledge
docker compose -f milvus_docker.yml up -d
# 等 1 分钟让三个容器转健康：docker ps 应看到 food-rag-milvus (healthy)
# 可选图形界面：docker compose -f milvus_docker.yml --profile tools up -d (attu, 端口8000)
```

### 3. Python 环境（与 CPU 机器的唯一差异在这里）

```bash
conda env create -f environment.yml
conda activate food-rag

# 关键：先装 CUDA 版 torch，再装依赖 docling 等
# 5060 是 Blackwell 架构(sm_120)，需要 CUDA 12.8+ 的轮子
pip install torch --index-url https://download.pytorch.org/whl/cu128
pip install -r requirements.txt

# 验证 GPU 可用（必须两行都正确输出）
python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
# 期望输出类似： True NVIDIA GeForce RTX 5060
```

> 注意：CPU 机器上 torch 装的是默认（CPU）版本；两台机器的 pip 依赖列表相同、只有 torch 轮子不同。这也是环境选择重建而不是拷贝的原因。

### 4. 前端

```bash
cd frontend && npm ci && npm run build
```

### 5. 启动与验证清单

```bash
# 回到项目根目录
python app.py            # 打开 http://127.0.0.1:7860
```

按顺序验证（每步失败都能把问题隔离在单一组件）：

1. `GET /api/documents` 返回旧文档列表 → SQLite 迁移 OK；
2. 界面上传一份小年报（如 corpus/raw/601555_东吴证券_2023.pdf）走完解析→向量→写入 → Milvus + DashScope API + 管线 OK；
3. 问一个该年报的问题，引用能点开原文 → 全链路 OK；
4. 删除旧文档（第二批重灌前清理干净）。

### 6. 第一个开发任务：给 docling 接上 GPU

当前代码里 docling 用 CPU 跑版面模型（4.4 秒/页）。GPU 加速需要改 `src/ingestion/docling_parser.py`，在 `PdfPipelineOptions` 上挂：

```python
from docling.datamodel.accelerator_options import AcceleratorOptions, AcceleratorDevice

pipeline_options.accelerator_options = AcceleratorOptions(
    num_threads=8,
    device=AcceleratorDevice.CUDA,   # 从环境变量读，默认 CPU，两台机器共用一套代码
)
```

验收：拿 002679_福建金森（160 页，CPU 实测 1038 秒）重新解析计时，记录提速比。这是迁移后的第一个实验，结论决定全量重灌的预估工时。

## 四、常见坑

| 坑 | 说明 |
| --- | --- |
| 忘了拷 `.env` | 应用起得来但 embedding/问答全报错（API key 缺失），且 `.env` 不在 git 里 |
| Docker Desktop 没装/没启动 | Milvus 连接报 `Connection refused: 19530` |
| 先装了 requirements 再装 CUDA torch | pip 可能重复解析 torch 版本；顺序一定是 **torch(CUDA) 在前** |
| 新机器路径不同 | 代码里无绝对路径依赖（全部相对项目根），换盘符/目录不需要改代码 |
| 旧文档检索不到 | 预期行为（向量没迁），按第二节处理：删除旧文档 + 重灌 |
