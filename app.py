"""应用启动入口。"""

from src.services.rag_service import build_service
from src.ui.gradio_app import build_app

if __name__ == "__main__":
    app = build_app(build_service())
    app.launch(server_name="0.0.0.0", server_port=7860)
