# 多阶段构建：先编前端，再装 Python 依赖，产出独立运行的应用镜像。
# torch 单独先装（CUDA/CPU 由 build-arg 切换），requirements 里的依赖看到
# torch 已满足就不会重复解析——这是控制"装哪个版本 torch"的唯一入口。

FROM node:20-alpine AS frontend
WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim
# 默认 CUDA 12.8 轮子（RTX 5060 Blackwell 需要）；本机无卡测试时用
# --build-arg TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu 构建 CPU 变体。
# CUDA 版 torch 在无 GPU 机器上也能正常运行（自动回退 CPU），所以目标机器
# 与测试机器可以共用同一个 Dockerfile。
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cu128
WORKDIR /app
RUN pip install --no-cache-dir torch --index-url ${TORCH_INDEX_URL}
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py ./
COPY src/ ./src/
COPY eval/ ./eval/
COPY --from=frontend /build/dist ./frontend/dist
ENV PDF_PARSER=docling
EXPOSE 7860
CMD ["python", "app.py"]
