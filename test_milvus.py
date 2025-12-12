from pymilvus import MilvusClient, DataType
from sentence_transformers import SentenceTransformer
import time

# --- 1. 连接到 Milvus ---
MILVUS_HOST = "127.0.0.1"
MILVUS_PORT = "19530"
client = MilvusClient(uri=f"tcp://{MILVUS_HOST}:{MILVUS_PORT}")
print(f"成功连接到 Milvus: {MILVUS_HOST}:{MILVUS_PORT}")

# --- 2. 定义集合和数据 ---
collection_name = "quickstart_milvus"
embedding_model = SentenceTransformer('all-MiniLM-L6-v2') # 加载嵌入模型
dimension = 384  # all-MiniLM-L6-v2 模型的维度

# 定义集合的 Schema
schema = MilvusClient.create_schema(
    auto_id=True, # 自动生成主键
    enable_dynamic_field=True, # 允许动态添加字段
)
schema.add_field(field_name="id", datatype=DataType.INT64, is_primary=True)
schema.add_field(field_name="embedding", datatype=DataType.FLOAT_VECTOR, dim=dimension)
schema.add_field(field_name="chunk_text", datatype=DataType.VARCHAR, max_length=65535)
schema.add_field(field_name="category", datatype=DataType.VARCHAR, max_length=255)

# 如果集合存在，则先删除
if client.has_collection(collection_name):
    client.drop_collection(collection_name)
    print(f"集合 '{collection_name}' 已存在，已删除重建。")

# 创建集合
client.create_collection(collection_name=collection_name, schema=schema)
print(f"集合 '{collection_name}' 创建成功。")

# 准备要插入的数据 (与 test_pinecone.py 相同)
records = [
    { "id": 1, "chunk_text": "The Eiffel Tower was completed in 1889 and stands in Paris, France.", "category": "history" },
    { "id": 2, "chunk_text": "Photosynthesis allows plants to convert sunlight into energy.", "category": "science" },
    { "id": 3, "chunk_text": "Albert Einstein developed the theory of relativity.", "category": "science" },
    { "id": 4, "chunk_text": "The mitochondrion is often called the powerhouse of the cell.", "category": "biology" },
    { "id": 5, "chunk_text": "Shakespeare wrote many famous plays, including Hamlet and Macbeth.", "category": "literature" },
    { "id": 7, "chunk_text": "The Great Wall of China was built to protect against invasions.", "category": "history" },
    { "id": 17, "chunk_text": "The Pyramids of Giza are among the Seven Wonders of the Ancient World.", "category": "history" },
    { "id": 21, "chunk_text": "The Statue of Liberty was a gift from France to the United States.", "category": "history" },
    { "id": 38, "chunk_text": "The Taj Mahal is a mausoleum built by Emperor Shah Jahan.", "category": "history" },
]

# --- 3. 生成嵌入并插入数据 ---
texts_to_embed = [record["chunk_text"] for record in records]
embeddings = embedding_model.encode(texts_to_embed)

for i, record in enumerate(records):
    record["embedding"] = embeddings[i]

client.insert(collection_name=collection_name, data=records)
print(f"成功插入 {len(records)} 条数据。")

# 强制刷新数据，确保数据段被密封
client.flush(collection_name=collection_name)
print("数据已刷新。")

# --- 4. 创建索引并加载集合 ---
index_params = client.prepare_index_params()
index_params.add_index(
    field_name="embedding",
    index_type="AUTOINDEX",
    metric_type="L2"
)
client.create_index(collection_name=collection_name, index_params=index_params)
print(f"集合 '{collection_name}' 的索引已创建。")

client.load_collection(collection_name=collection_name)
print(f"集合 '{collection_name}' 已加载到内存。")

# 等待加载完成
time.sleep(2)

# --- 5. 执行相似性搜索 ---
query_text = "Famous historical structures and monuments"
query_embedding = embedding_model.encode([query_text])[0]

search_params = {"metric_type": "L2", "params": {}}

search_results = client.search(
    collection_name=collection_name,
    data=[query_embedding],
    limit=5,
    search_params=search_params,
    output_fields=["chunk_text", "category"]
)

# --- 6. 打印搜索结果 ---
print("\n--- 原始搜索结果 ---")
print(search_results)

print("\n--- 格式化搜索结果 ---")
if not search_results or not search_results[0]:
    print("没有找到匹配的结果。")
else:
    for result in search_results[0]:
        print(f"ID: {result['id']}, 距离: {result['distance']:.2f}, 分类: {result['entity']['category']}, 文本: {result['entity']['chunk_text']}")

# --- 7. 清理 ---

client.close()
print("\nMilvus 客户端连接已关闭。")

