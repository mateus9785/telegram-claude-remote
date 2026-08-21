"""Testes de config.BotState. lock_to escreve no .env de verdade (via ENV_PATH), então
os testes que chamam lock_to redirecionam ENV_PATH pra um arquivo temporário."""

from pathlib import Path

import pytest

from telegram_claude_remote.config import BotState


class TestIsOwner:
    def test_sem_dono_travado_ninguem_e_owner(self) -> None:
        state = BotState()
        assert state.is_owner(123) is False

    def test_com_dono_travado_so_ele_e_owner(self) -> None:
        state = BotState(owner_chat_id="123")
        assert state.is_owner(123) is True
        assert state.is_owner("123") is True
        assert state.is_owner(456) is False


class TestLockTo:
    def test_trava_no_chat_certo(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        env_path = tmp_path / ".env"
        env_path.write_text("TELEGRAM_BOT_TOKEN=x\n")
        monkeypatch.setattr("telegram_claude_remote.config.ENV_PATH", str(env_path))

        state = BotState()
        state.lock_to(123)

        assert state.is_owner(123) is True
        assert state.is_owner(456) is False

    def test_persiste_no_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        env_path = tmp_path / ".env"
        env_path.write_text("TELEGRAM_BOT_TOKEN=x\n")
        monkeypatch.setattr("telegram_claude_remote.config.ENV_PATH", str(env_path))

        state = BotState()
        state.lock_to(789)

        assert "OWNER_CHAT_ID=789" in env_path.read_text()
