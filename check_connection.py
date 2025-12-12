import os
import requests
from dotenv import load_dotenv

# 加载 .env 文件中的环境变量
load_dotenv()

DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY")
DASHSCOPE_EMBEDDING_URL = "https://dashscope.aliyuncs.com/api/v1/services/embeddings/text-embedding/text-embedding"

if not DASHSCOPE_API_KEY or DASHSCOPE_API_KEY == 'your_dashscope_api_key':
    print("错误：请确保您的 .env 文件中正确设置了 DASHSCOPE_API_KEY。")
else:
    headers = {
        "Authorization": f"Bearer {DASHSCOPE_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "model": "text-embedding-v2",
        "input": {
            "texts": ["这是一个连接测试。"]
        }
    }

    print(f"正在尝试连接到: {DASHSCOPE_EMBEDDING_URL}")
    try:
        # 设置20秒超时
        response = requests.post(DASHSCOPE_EMBEDDING_URL, headers=headers, json=data, timeout=20)
        response.raise_for_status()  # 如果状态码是 4xx 或 5xx，则抛出异常
        print("连接成功！")
        print("服务器响应:")
        print(response.json())
    except requests.exceptions.RequestException as e:
        print(f"连接失败。错误信息如下:")
        print(e)
