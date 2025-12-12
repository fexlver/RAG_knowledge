from pymilvus import connections, Collection, utility
import os
from dotenv import load_dotenv

load_dotenv()

# 连接 Milvus
print("正在连接 Milvus...")
connections.connect(
    alias="default",
    host=os.getenv("MILVUS_HOST", "localhost"),
    port=os.getenv("MILVUS_PORT", "19530")
)

collection_name = "food_safety_collection"

if utility.has_collection(collection_name):
    c = Collection(collection_name)
    print(f"\n🔍 集合 '{collection_name}' 存在。")
    print("-" * 30)
    print(f"Schema (表结构): {c.schema}")
    print("-" * 30)

    # 重点看这一行
    import json

    # 尝试解析一下 auto_id
    # 通常 schema 里会直接显示 auto_id: False/True
    print(f"👉 核心证据 - AutoID 状态: {c.schema.auto_id}")

    if c.schema.auto_id == False:
        print("❌ 结论：实锤了！数据库里存的就是 False (手动ID)。")
        print("   这就是为什么你不传 ID 会报错的原因。")
    else:
        print("✅ 结论：是 True。如果还是报错，那真是见鬼了（通常不会到这一步）。")
else:
    print(f"❓ 集合 '{collection_name}' 根本不存在。那你的代码应该会自动新建一个 True 的才对。")