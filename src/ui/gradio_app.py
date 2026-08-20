"""食品安全知识库 Gradio 界面。"""

from __future__ import annotations

import gradio as gr

from src.services.rag_service import FoodSafetyRAGService


def build_app(service: FoodSafetyRAGService) -> gr.Blocks:
    initial_session = service.new_session()
    with gr.Blocks(title="食品安全知识库问答系统") as demo:
        gr.Markdown(
            "# 食品安全知识库问答系统\n"
            "混合检索、标准版本比较、可追溯引用与低置信拒答。回答仅供资料检索，合规决策请核对现行原文。"
        )
        with gr.Tab("知识问答"):
            with gr.Row():
                session = gr.Dropdown(
                    choices=service.session_choices(),
                    value=initial_session,
                    label="当前会话",
                    scale=5,
                )
                new_session = gr.Button("新建会话")
                refresh_sessions = gr.Button("刷新列表")
                delete_session = gr.Button("删除会话", variant="stop")
            chatbot = gr.Chatbot(height=520)
            question = gr.Textbox(
                label="问题",
                placeholder="例如：GB 2760 的适用范围是什么？请标明依据。",
            )
            trace = gr.JSON(label="检索执行轨迹")
            submit = gr.Button("检索并回答", variant="primary")

            def session_dropdown(selected: str | None = None):
                choices = service.session_choices()
                value = selected or (
                    choices[0][1] if choices else service.new_session()
                )
                return gr.Dropdown(choices=service.session_choices(), value=value)

            def create_session():
                session_id = service.new_session()
                return session_dropdown(session_id), []

            def remove_session(session_id: str):
                next_session = service.delete_session(session_id)
                return session_dropdown(next_session), []

            def load_session(session_id: str):
                return service.load_session(session_id)

            def ask(message: str, history: list[dict], session_id: str):
                history = history or []
                try:
                    result = service.ask(message, session_id)
                    history.extend(
                        [
                            {"role": "user", "content": message},
                            {"role": "assistant", "content": result.answer},
                        ]
                    )
                    return "", history, result.trace
                # UI 边界统一接住模型、数据库和网络异常，避免单次请求终止服务。
                except Exception as error:  # noqa: BLE001
                    history.append(
                        {"role": "assistant", "content": f"请求失败：{error}"}
                    )
                    return message, history, [f"异常：{error}"]

            submit.click(ask, [question, chatbot, session], [question, chatbot, trace])
            question.submit(
                ask, [question, chatbot, session], [question, chatbot, trace]
            )
            new_session.click(create_session, outputs=[session, chatbot])
            refresh_sessions.click(session_dropdown, [session], session)
            delete_session.click(remove_session, [session], [session, chatbot])
            session.change(load_session, session, chatbot)

        with gr.Tab("知识库管理"):
            files = gr.File(
                label="上传 PDF/TXT", file_count="multiple", type="filepath"
            )
            duplicate_mode = gr.Radio(
                choices=[("跳过同名文件", "skip"), ("覆盖同名文件", "overwrite")],
                value="skip",
                label="同名文件处理",
            )
            upload = gr.Button("建立索引", variant="primary")
            upload_result = gr.Dataframe(
                headers=["文件", "状态", "文本块数", "说明"], interactive=False
            )
            gr.Markdown("### 已入库文档")
            refresh_documents = gr.Button("刷新文档列表")
            document_table = gr.Dataframe(
                value=service.document_rows(),
                headers=["文档ID", "文件", "标准号", "类型", "有效性", "入库时间"],
                interactive=False,
            )
            document_to_delete = gr.Dropdown(
                choices=service.document_choices(), label="选择要删除的文档"
            )
            delete_document = gr.Button("删除文档及索引", variant="stop")

            def ingest(file_paths: list[str] | None, mode: str):
                rows = []
                for file_path in file_paths or []:
                    try:
                        result = service.ingestion.ingest(file_path, mode)
                        rows.append(
                            [
                                result.file_name,
                                result.status,
                                result.chunk_count,
                                result.detail,
                            ]
                        )
                    # 批量上传时隔离单文件失败，让其他文件继续处理。
                    except Exception as error:  # noqa: BLE001
                        rows.append([str(file_path), "failed", 0, str(error)])
                return rows

            upload.click(ingest, [files, duplicate_mode], upload_result)

            def refresh_document_components():
                return service.document_rows(), gr.Dropdown(
                    choices=service.document_choices(), value=None
                )

            def remove_document(doc_id: str):
                if not doc_id:
                    raise gr.Error("请先选择文档。")
                service.ingestion.delete(doc_id)
                return refresh_document_components()

            refresh_documents.click(
                refresh_document_components,
                outputs=[document_table, document_to_delete],
            )
            delete_document.click(
                remove_document,
                document_to_delete,
                [document_table, document_to_delete],
            )
    return demo
