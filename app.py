"""FastAPI 应用启动入口。"""

import uvicorn

from src.api import create_app

app = create_app()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=7860)
