# **🤖 Qwen-Milvus RAG: 基于通义千问与 Milvus 的智能文档问答系统**

## **📖 项目简介**

本项目实现了一个基于 **RAG (Retrieval-Augmented Generation)** 架构的可视化文档问答系统。系统利用 **Docker** 部署高性能向量数据库 **Milvus**，结合 **Attu** (Zilliz UI) 进行可视化管理。后端核心逻辑集成了 **阿里云通义千问 API**，实现了从文本嵌入 (Embedding) 到大模型生成 (Generation) 的全流程。  
该系统不仅仅是一个简单的问答 Demo，更包含了一系列生产级特性，如数据分片策略、文档去重、混合召回策略、上下文记忆以及多会话窗口管理。

## **✨ 核心功能**

### **1\. 🧠 强大的 RAG 引擎**

* **大模型支持**：无缝对接阿里云**通义千问**大模型 API，提供流畅的自然语言理解与生成。  
* **高性能检索**：使用 **Milvus** 作为向量检索引擎，支持亿级数据规模的毫秒级响应。  
* **嵌入模型**：使用通义千问 Embeddings 模型将非结构化文本转化为高维向量。

### **2\. 📚 智能知识库管理**

* **批量上传**：支持用户批量上传 PDF、TXT 格式文档。  
* **智能分片 (Chunking)**：内置优化的数据分片策略，根据语义和字符长度自动切割文档，保留上下文完整性。  
* **增量更新与去重**：  
  * **重复检测**：在存入向量库前自动计算指纹，防止重复文档造成的存储浪费和检索干扰。  
  * **增删管理**：支持对已建立索引的文档进行删除和更新操作。

### **3\. 🔍 高级检索与问答**

* **优化召回策略**：不仅依靠单一的向量相似度，还结合了关键词匹配或重排序 (Rerank) 策略，提高上下文召回的精准度。  
* **上下文记忆 (Context Memory)**：系统维护对话历史，模型能够理解“它”、“上面提到的”等指代词，实现流畅的多轮对话。  
* **多会话窗口**：支持开启多个独立的聊天窗口，不同话题之间的上下文互不干扰。

### **4\. 🖥️ 可视化交互**

* **Docker 部署**：一键启动 Milvus 和 Attu 管理界面。  
* **交互式 UI**：通过 qa\_system.py 提供友好的问答界面，实时查看检索到的参考文档片段。

## **🏗️ 系统架构**

| 组件 | 技术选型 | 说明 |
| :---- | :---- | :---- |
| **应用层** | Python (Streamlit/Gradio) | qa\_system.py: 负责 UI 交互、业务逻辑与状态管理 |
| **模型层** | 阿里云 DashScope SDK | 调用通义千问 LLM 和 Embedding API |
| **存储层** | Milvus (Docker) | 存储向量数据 (Vector Store) |
| **管理层** | Attu (Docker) | 向量数据库的可视化管理工具 |

## **🚀 快速开始**

### **1\. 环境准备**

确保您的环境已安装：

* [Docker](https://www.docker.com/) & [Docker Compose](https://docs.docker.com/compose/)  
* Python 3.8+  
* Git

### **2\. 获取 API Key**

前往 [阿里云百炼控制台](https://dashscope.console.aliyun.com/) 获取通义千问的 API Key。

### **3\. 启动向量数据库 (Milvus & Attu)**

在项目根目录下运行 Docker Compose 命令：  
#### 进入配置文件所在目录
cd /path/to/your/file

#### 启动服务（后台运行加 -d 参数）
docker-compose -f docker-compose.yml up -d

启动后，您可以通过浏览器访问 http://localhost:8000 进入 Attu 可视化管理界面，查看向量库状态。

### **4\. 安装 Python 依赖**

pip install \-r requirements.txt

### **5\. 运行问答系统**

设置环境变量（建议写入 .env 文件或直接在终端导出）：  

\# Windows (PowerShell)  
$env:DASHSCOPE\_API\_KEY="sk-xxxxxxxxxxxxxxxxxxxxxxxx"

启动主程序：  
python qa\_system.py

## **📂 文件目录说明**

.  
├── qa\_system.py           \# 🚀 系统主入口：包含 UI、RAG 逻辑、会话管理  
├── docker-compose.yml     \# 🐳 Milvus 和 Attu 的容器配置文件  
├── requirements.txt       \# 📦 Python 依赖列表  
└── README.md              \# 📄 项目说明文档
└── test_milvus.py         \# 🧪 测试 Milvus 连接与向量操作  
└── localDocument_embeding.py \# 手动文档嵌入脚本
└── API_test.py             \# 🧪 测试 DashScope API 连接与调用
└── check_connection.py     \# 🧪 测试 DashScope Embedding 模型连接与调用


## **🛠️ 配置说明**

您可以在 qa\_system.py 中调整以下参数以优化性能：

* CHUNK\_SIZE: 文本分片大小 (建议 500-1000 tokens)。  
* TOP\_K: 检索召回的片段数量 (建议 3-5)。  
* SIMILARITY\_THRESHOLD: 向量相似度阈值，用于过滤低质量召回。

## **🤝 贡献与致谢**

* 感谢 **Milvus** 社区提供优秀的开源向量数据库。  
* 感谢 **阿里云 Qwen** 团队提供的大模型 API 支持。

如有问题，欢迎提交 Issue 或 Pull Request！