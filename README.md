# 基于 Qwen 与 Milvus 的食品安全知识库问答系统

面向食品安全标准、法规、公告和指南资料分散、人工定位条款效率低的问题，本项目提供文档入库、混合检索、版本比较、可追溯问答和离线评测能力。系统不会把“检索到内容”直接等同于“结论正确”：当证据相关性不足时主动拒答，并提示用户核对主管部门发布的现行原文。

## 核心能力

- PDF/TXT 按页解析，递归切分时保留文件名、页码、章节、标准号、发布日期、实施日期和有效性等元数据。
- 使用 SHA-256 内容指纹去重；同名不同内容可选择跳过或覆盖，避免重复向量化和索引污染。
- `Milvus Dense Top-K + SQLite FTS5 中文关键词 Top-K + RRF + Qwen Rerank` 四阶段检索，兼顾语义问题和标准号、条款号等精确词。
- 对普通问题执行直接检索；对新旧标准、版本差异和有效性问题执行多步检索与证据融合，并在界面展示执行轨迹。
- 回答附文件、标准号、页码和章节引用；最高重排分低于阈值时拒答，降低无依据生成。
- SQLite 持久化文档账本、会话、消息和操作日志；Gradio 界面支持多会话切换/删除及文档索引管理。
- `test/` 提供离线单元测试，`eval/` 提供引用率、拒答准确率、关键词命中率和延迟评测入口。

## 系统链路

```text
PDF/TXT -> 解析/元数据 -> 内容指纹 -> 分块 -> Qwen Embedding -> Milvus
                                      \-> 中文分词 -> SQLite FTS5

问题 -> 历史改写 -> 查询路由 -> Dense + Keyword -> RRF -> Rerank
     -> 置信阈值 -> Qwen 生成 -> 文件/页码/章节引用
```

这里的 Agent 指“查询路由 + 多步检索工具编排 + 可观测轨迹”。普通问题仍走成本更低的直接 RAG，不为了概念包装而强制执行多轮模型调用。

## 快速启动（Windows PowerShell）

### 1. 创建 Conda 环境

项目已经按 Python 3.11 配置。推荐使用项目内环境，避免与其他项目冲突：

```powershell
$env:CONDA_PKGS_DIRS = "$PWD\.conda\pkgs"
conda env create --prefix .\.conda\envs\food-rag --file environment.yml
conda activate .\.conda\envs\food-rag
```

若环境已创建，仅需激活：

```powershell
conda activate .\.conda\envs\food-rag
```

### 2. 配置密钥

```powershell
Copy-Item .env.example .env
```

然后只在本地 `.env` 中填写 `DASHSCOPE_API_KEY`。`.env`、数据库、上传文件和 Conda 环境均已加入 `.gitignore`，禁止提交真实密钥。

### 3. 启动 Milvus

```powershell
docker compose -f milvus_docker.yml up -d
```

如需 Attu 管理界面：

```powershell
docker compose -f milvus_docker.yml --profile tools up -d
```

Attu 地址为 `http://localhost:8000`，Milvus 地址为 `127.0.0.1:19530`。

### 4. 启动应用

```powershell
python app.py
```

访问 `http://localhost:7860`，先在“知识库管理”上传资料，再在“知识问答”提问。

## 测试与评测

不依赖外部模型和 Milvus 的单元测试：

```powershell
pytest -q
```

启动 Milvus、配置密钥并准备知识库后运行端到端评测：

```powershell
python eval/run_eval.py --dataset eval/dataset.jsonl
```

结果写入 `eval/results/latest.json`，主要观察：

- `citation_rate`：可回答问题是否给出依据；
- `refusal_accuracy`：资料不足问题是否正确拒答；
- `keyword_hit_rate`：回答是否覆盖预期要点；
- `average_latency_seconds`：端到端平均耗时。

## 目录结构

```text
src/agent/       查询路由与多步检索编排
src/config/      环境配置
src/domain/      领域模型
src/generation/  引用构造与拒答控制
src/ingestion/   解析、元数据、分块与入库
src/models/      Qwen 模型网关
src/retrieval/   混合召回与 RRF 融合
src/services/    应用用例与依赖装配
src/storage/     SQLite 与 Milvus 适配器
src/ui/          Gradio 界面
test/            离线测试
eval/            端到端评测集与脚本
```

## 使用边界

食品安全标准会修订或废止，自动抽取的有效性状态只能用于检索辅助，不能替代法规核验。生产使用还应接入权威标准版本源、访问控制、操作审计、敏感文档隔离和人工复核流程。
