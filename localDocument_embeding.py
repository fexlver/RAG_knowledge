
import os
from dotenv import load_dotenv
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import PyPDFLoader, DirectoryLoader
from langchain_community.embeddings import DashScopeEmbeddings
from langchain_community.vectorstores import Milvus

# 加载环境变量
def load_env_variables():
    load_dotenv()
    return {
        "dashscope_api_key": os.getenv("DASHSCOPE_API_KEY"),
        "milvus_host": os.getenv("MILVUS_HOST"),
        "milvus_port": os.getenv("MILVUS_PORT"),
    }

# 读取和处理文档
def read_and_process_documents(data_path):
    # 同时处理txt和pdf
    text_loader = DirectoryLoader(data_path, glob="**/*.txt")
    pdf_loader = DirectoryLoader(data_path, glob="**/*.pdf", loader_cls=PyPDFLoader)
    text_documents = text_loader.load()
    pdf_documents = pdf_loader.load()
    documents = text_documents + pdf_documents
    # 切分重叠
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=500,  # 中文通常比英文密度大，建议适当调小
        chunk_overlap=100,
        separators=["\n\n", "\n", "。", "！", "？", "，", " ", ""]  # 优先按段落、句号切分
    )
    return text_splitter.split_documents(documents)

# 创建并存储嵌入
def create_and_store_embeddings(texts, dashscope_api_key, milvus_host, milvus_port):
    embeddings = DashScopeEmbeddings(model="text-embedding-v2", dashscope_api_key=dashscope_api_key)
    vector_store = Milvus.from_documents(
        texts,
        embeddings,
        connection_args={"host": milvus_host, "port": milvus_port},
        collection_name="food_safety_collection",
    )
    return vector_store


def main():
    # 加载环境变量
    env_vars = load_env_variables()

    # 设置数据路径
    data_path = "data/"

    # 读取和处理文档
    texts = read_and_process_documents(data_path)

    # 创建并存储嵌入
    vector_store = create_and_store_embeddings(
        texts,
        env_vars["dashscope_api_key"],
        env_vars["milvus_host"],
        env_vars["milvus_port"],
    )

    print("数据嵌入完成并已存入Milvus。")


if __name__ == "__main__":
    main()
