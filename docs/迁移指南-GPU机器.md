# 迁移指南：把工作台搬到有 RTX 5060 的 Windows 机器（Docker 一键版）

> 目标：新机器成为"解析工作站"（GPU 加速入库 + 全量重灌）。
> **推荐路径：Docker 一键部署**——应用已容器化（Dockerfile + docker-compose.yml），
> 新机器只需要装一个 Docker Desktop，不再需要 conda / node / pip。

## 一、要搬什么

| 内容 | 位置 | 体量 | 搬法 |
| --- | --- | --- | --- |
| 项目目录 | 整个 `RAG_knowledge/`（含 `.git`、`Dockerfile`、compose 文件） | 几 MB | U 盘/局域网 |
| 年报语料 | `corpus/raw/` | 460 MB | 随目录 |
| 业务数据 | `data/`（SQLite + uploads） | 88 MB | 随目录 |
| API 密钥 | `.env` | <1 KB | 随目录（**格式必须是"无引号无行内注释"**，见第四节） |
| Docker 镜像 | - | - | 新机器 `docker compose up --build` 现场构建（首构建约下载 10GB，一次性） |
| Milvus 向量数据 | Docker 卷 | ~几百 MB | **不搬**，重灌重建（理由见第二节） |

不需要拷贝：`.conda/`、`node_modules/`、`frontend/dist`、`.pytest_tmp`（都会重建）。

```bash
# 本机打包（Git Bash）
cd /e/A_project
tar --exclude='RAG_knowledge/.conda' \
    --exclude='RAG_knowledge/frontend/node_modules' \
    --exclude='RAG_knowledge/frontend/dist' \
    --exclude='RAG_knowledge/.pytest_tmp' \
    -czf RAG_knowledge_migrate.tar.gz RAG_knowledge
```

## 二、Milvus 向量数据为什么不搬（数据集策略）

向量库（13,000+ 块的 embedding）**丢弃，重新上传生成**：

1. 计划本来就是全量重灌——断数字修复、小表不拆分、表格实体前缀三个确诊问题都要重灌根治；
2. 重灌原料（corpus/raw 的 100 份 PDF）随机器走，embedding 走 DashScope API 与机器无关；
3. 为一次性数据引入 milvus-backup 迁移工具链不值。

**连带影响**：SQLite 里的旧文档记录指向不存在的向量——旧文档可见但检索不到。处理：照常拷 `data/`（保留会话历史与配置），到新机器后删除旧文档、重新上传全量语料。

## 三、新机器三步走

### 1. 装软件（唯一的前置）

- **Docker Desktop**（WSL2 后端，安装时默认）+ 显卡驱动
- 验证 GPU 直通可用：
  ```powershell
  docker run --rm --gpus all nvidia/cuda:12.8.1-base-ubuntu22.04 nvidia-smi
  # 能列出 RTX 5060 即通
  ```

### 2. 解压项目，改 .env

`.env` 里确认 `DASHSCOPE_API_KEY=` 已填（其余项有默认值）。

### 3. 一键启动

```bash
cd RAG_knowledge
docker compose -f docker-compose.yml -f compose.gpu.yml up -d --build
# 首次构建约 10-20 分钟（下载 CUDA torch 等约 10GB），之后秒级启动
docker compose ps   # 等四个容器都 healthy/running
```

打开 `http://127.0.0.1:7860`。

**验证清单**（每步隔离一个组件）：

1. 界面能看到旧文档列表 → SQLite（挂载卷）OK；
2. 上传一份小年报走完 解析→向量→写入 → Milvus + DashScope + 管线 OK；
3. 提问并点开引用 → 全链路 OK（含容器内凭据回退：读 .env 的 DASHSCOPE_API_KEY）；
4. 容器内验证 GPU：`docker compose exec app python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"` → `True NVIDIA GeForce RTX 5060`。

之后删除旧文档，开始全量重灌。评测在容器内跑：`docker compose exec app python eval/run_eval.py --tier retrieval`。

### GPU 直通是什么

容器里的程序默认**看不到宿主机显卡**。`compose.gpu.yml` 里的 `deploy.resources.reservations.devices` 声明把 NVIDIA 设备透传进容器，Docker Desktop（WSL2）+ 官方驱动即可，容器内不需要装驱动。`docker-compose.yml` 默认不带这段（无卡机器也能起），GPU 机器用 `-f compose.gpu.yml` 叠加。

## 四、.env 格式约束（重要）

Docker 的 `--env-file` 不解析引号和行内注释，与 python-dotenv 不同。**格式必须是**：

```ini
# 注释独立成行（行首 # 可以）
DASHSCOPE_API_KEY=sk-xxx
MILVUS_HOST=127.0.0.1
MILVUS_PORT=19530
```

不要写 `MILVUS_PORT="19530" # 端口` 这种（引号和注释会变成值的一部分，应用启动即报错）。本项目 `.env` 已按此规范重写过；新增配置项时遵守。

注意：compose 里 `environment:` 的 `MILVUS_HOST: milvus` 会覆盖 `.env` 的 127.0.0.1（容器内访问同网络的 milvus 服务），这是预期行为。

## 五、常用运维命令

```bash
docker compose logs -f app                    # 看应用日志
docker compose restart app                    # 重启应用
docker compose up -d --build                  # 改代码后重建
docker compose exec app python -m pytest test/ -q   # 容器内跑测试
docker compose down                           # 停止（数据卷保留）
```

数据持久化位置：SQLite 与 uploads 在宿主机 `./data/`（容器重建不丢）；docling 模型权重在具名卷 `docling-cache`（首次解析下载约 2GB，之后复用）。

## 六、本机（无卡）开发模式（可选）

本机继续用 conda 直跑不受影响：`python app.py`。两台机器同一套代码；若想本机也容器化跑 CPU 版：

```bash
docker build --build-arg TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu -t food-rag-app:cpu .
docker compose up -d    # 不带 gpu 覆盖文件
```

## 七、常见坑

| 坑 | 现象与处理 |
| --- | --- |
| 忘拷 `.env` | 应用起得来但问答/向量化报错（API key 缺失） |
| .env 带引号/行内注释 | 启动即 `ValueError: invalid literal for int()`（本机已踩过） |
| 镜像源失效 | `docker pull` 报 mirror EOF：直接拉取通常可行（本机实测），或换可用镜像源后重启 Docker |
| Docker Desktop 未就绪 | Milvus 连接 `Connection refused: 19530` |
| 首次构建慢 | CUDA torch 约 10GB，属正常；`docling-cache` 卷首次解析再下载 2GB 模型 |
| 旧文档检索不到 | 预期（向量未迁）：删除旧文档 + 重灌 |
| GPU 容器起不来 | 确认用的是 `-f compose.gpu.yml` 叠加且驱动正常；无卡机器去掉该文件即可 |
