from src.ui.gradio_app import build_app


class FakeService:
    def new_session(self) -> str:
        return "test-session"

    def session_choices(self):
        return [("新对话", "test-session")]

    def document_rows(self):
        return []

    def document_choices(self):
        return []


def test_gradio_interface_can_be_built_with_current_version():
    app = build_app(FakeService())

    assert app is not None
