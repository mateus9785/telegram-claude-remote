"""Testes de handlers.parse_command e handlers._mensagem_reaberta (funções puras)."""

from telegram_claude_remote import handlers


class TestParseCommand:
    def test_claude_com_argumento(self) -> None:
        assert handlers.parse_command("/claude faz algo") == ("/claude", "faz algo")

    def test_claude_sem_argumento(self) -> None:
        assert handlers.parse_command("/claude") == ("/claude", "")

    def test_status(self) -> None:
        assert handlers.parse_command("/status") == ("/status", "")

    def test_reabrir_com_argumento(self) -> None:
        assert handlers.parse_command("/reabrir abc123") == ("/reabrir", "abc123")

    def test_reabrir_sem_argumento(self) -> None:
        assert handlers.parse_command("/reabrir") == ("/reabrir", "")

    def test_pastas(self) -> None:
        assert handlers.parse_command("/pastas") == ("/pastas", "")

    def test_start(self) -> None:
        assert handlers.parse_command("/start") == ("/start", "")

    def test_texto_solto_nao_e_comando(self) -> None:
        assert handlers.parse_command("oi tudo bem?") == (None, "")

    def test_comando_desconhecido(self) -> None:
        assert handlers.parse_command("/desconhecido") == (None, "")


class TestMensagemReaberta:
    def test_com_novo_id(self) -> None:
        msg = handlers._mensagem_reaberta("abc123", "https://claude.ai/code/session_abc123")
        assert "abc123" in msg
        assert "🔓" in msg

    def test_sem_novo_id(self) -> None:
        msg = handlers._mensagem_reaberta(None, None)
        assert "novo id" not in msg
        assert "🔓" in msg
