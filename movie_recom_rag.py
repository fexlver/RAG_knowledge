from API_test import OpenAI
from pymilvus import MilvusClient, DataType
from datasets import load_dataset
from tqdm import tqdm
import textwrap
import os
from langchain_community.embeddings import DashScopeEmbeddings
from dotenv import load_dotenv
load_dotenv(".env")

# 初始化
embeddings_model = DashScopeEmbeddings(
    model="text-embedding-v2",  # 通义千问的通用嵌入模型
    dashscope_api_key=os.getenv("DASHSCOPE_API_KEY")  # 从环境变量获取并传入 API Key
)

COLLECTION_NAME = "movie_search"
DIMENSION = 1536
BATCH_SIZE = 10  # 通义 API 的单个请求中的批量大小（batch size）不能大于 10

MILVUS_HOST = os.getenv("MILVUS_HOST") or "127.0.0.1"
MILVUS_PORT = int(os.getenv("MILVUS_PORT") or "19530")

try:
    # 创建 Milvus 客户端实例
    client = MilvusClient(uri=f"tcp://{MILVUS_HOST}:{MILVUS_PORT}")

    recreate = False
    # 判断 Collection 是否已存在
    if client.has_collection(COLLECTION_NAME):
        print(f"Collection '{COLLECTION_NAME}' 已存在。")
        user_input = input("是否删除并重新插入数据？输入 'yes' 删除重建，其它键跳过插入：").strip().lower()
        if user_input == "yes":
            client.drop_collection(COLLECTION_NAME)
            recreate = True
        else:
            print("跳过数据插入部分。")
    else:
        recreate = True

    if recreate:
        # 定义 Collections 的字段，包括 id、标题、类型、发布年份、评级和描述。
        schema = MilvusClient.create_schema(auto_id=True, enable_dynamic_field=False)
        schema.add_field("id", DataType.INT64, is_primary=True)
        schema.add_field("title", DataType.VARCHAR, max_length=64000)
        schema.add_field("type", DataType.VARCHAR, max_length=64000)
        schema.add_field("release_year", DataType.INT64)
        schema.add_field("rating", DataType.VARCHAR, max_length=64000)
        schema.add_field("description", DataType.VARCHAR, max_length=64000)
        schema.add_field("embedding", DataType.FLOAT_VECTOR, dim=DIMENSION)

        client.create_collection(collection_name=COLLECTION_NAME, schema=schema)
        print("Collection 创建成功！")

        # 在 Collections 上创建索引并加载
        index_params = client.prepare_index_params()
        index_params.add_index("embedding", metric_type="IP", index_type="AUTOINDEX", params={})
        client.create_index(COLLECTION_NAME, index_params)
        client.load_collection(COLLECTION_NAME, replica_number=1)

        """
        在这个示例中，我们使用 HuggingLearners 的 netflix-shows 数据集。
        该数据集包含 8000 多部电影及其元数据对。：实测8807条
        我们将嵌入每条描述，并将其与标题、类型、发行年份和评分一起存储在 Milvus 中。
        """
        dataset = load_dataset("hugginglearners/netflix-shows", split="train")



        # 嵌入函数：接收文本列表，返回嵌入向量列表
        def emb_texts(texts):
            return embeddings_model.embed_documents(texts)

        # 遍历所有条目，分批进行嵌入和插入，使用 tqdm 可视化进度
        batch = []
        for i in tqdm(range(len(dataset))):
            batch.append({
                "title": dataset[i]["title"] or "",
                "type": dataset[i]["type"] or "",
                "release_year": dataset[i]["release_year"] or -1,
                "rating": dataset[i]["rating"] or "",
                "description": dataset[i]["description"] or "",
            })
            # 达到批次大小或遍历结束时批量插入
            if len(batch) % BATCH_SIZE == 0 or i == len(dataset) - 1:
                embeddings = emb_texts([item["description"] for item in batch])
                for item, emb in zip(batch, embeddings):
                    item["embedding"] = emb
                client.insert(COLLECTION_NAME, data=batch)
                batch = []
        print("数据插入完成！")

    else:
        # Collection 已存在且选择不重建，直接加载
        client.load_collection(collection_name=COLLECTION_NAME, replica_number=1)

except Exception as e:
    print(f"连接 Milvus 失败: {e}")
    exit(1)


"""
数据安全地插入 Milvus 后，我们就可以执行查询了。
查询将输入一个元组，其中包括要搜索的电影描述和要使用的过滤器。
这里的过滤器可以理解为mysql中的筛选。=><
搜索首先会打印出描述和过滤器表达式。
然后，我们会为每个结果打印得分、标题、类型、发行年份、评分和结果电影的描述。
"""
# 查询函数：循环问问题，支持描述和表达式输入，返回前3个结果
def start_query_loop():
    def emb_texts(texts):
        return embeddings_model.embed_documents(texts)

    print("\n进入搜索模式（输入 Ctrl+C 退出）\n")
    while True:
        try:
            # 交互式输入查询描述
            text = input("请输入你要搜索的描述 (Description)：").strip()
            if not text:
                print("描述不能为空。")
                continue

            # 交互式输入筛选表达式，如：release_year > 2015 and rating like 'PG%'
            expr = input("请输入筛选表达式 (Expression)，如 release_year > 2015 and rating like 'PG%'：").strip()
            print(f"\n 正在查询...\n描述: {text}\n筛选表达式: {expr or '无'}")

            # 调用 Milvus 搜索接口
            res = client.search(
                collection_name=COLLECTION_NAME,
                data=emb_texts([text]),
                filter=expr if expr else None,
                limit=3,  # 只返回前3个结果
                output_fields=["title", "type", "release_year", "rating", "description"],
                search_params={"metric_type": "IP", "params": {}},
            )

            # 结果输出，打印排名、得分、标题、类型、年份、分级、描述
            for hit_group in res:
                print("\n 查询结果：")
                for rank, hit in enumerate(hit_group, start=1):
                    entity = hit["entity"]
                    print(f"\t排名: {rank} 得分: {hit['distance']:.4f} 标题: {entity.get('title', '')}")
                    print(f"\t类型: {entity.get('type', '')} 年份: {entity.get('release_year', '')} 分级: {entity.get('rating', '')}")
                    print(textwrap.fill(entity.get("description", ""), width=88))
                    print("-" * 80)

        except KeyboardInterrupt:
            print("\n 退出查询模式。")
            break
        except Exception as err:
            print(f" 查询出错: {err}\n")

# 启动查询循环
start_query_loop()
