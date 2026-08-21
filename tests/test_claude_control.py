"""Testes das funções puras/quase-puras de claude_control, com subprocess/os mockados
(nunca chama `claude` de verdade)."""

import signal
import subprocess
from unittest.mock import MagicMock, patch

from telegram_claude_remote import claude_control


class TestIdDeSessaoValido:
    def test_aceita_alfanumerico_com_hifen(self) -> None:
        assert claude_control._id_de_sessao_valido("a1b2-c3d4") is True

    def test_recusa_path_traversal(self) -> None:
        assert claude_control._id_de_sessao_valido("../etc") is False

    def test_recusa_barra(self) -> None:
        assert claude_control._id_de_sessao_valido("abc/def") is False

    def test_recusa_string_vazia(self) -> None:
        assert claude_control._id_de_sessao_valido("") is False

    def test_recusa_none(self) -> None:
        assert claude_control._id_de_sessao_valido(None) is False


class TestMontarUrlRemote:
    def test_remove_prefixo_cse(self) -> None:
        assert claude_control.montar_url_remote("cse_abc123") == "https://claude.ai/code/session_abc123"

    def test_sem_prefixo_mantem_id(self) -> None:
        assert claude_control.montar_url_remote("abc123") == "https://claude.ai/code/session_abc123"

    def test_none_retorna_none(self) -> None:
        assert claude_control.montar_url_remote(None) is None

    def test_vazio_retorna_none(self) -> None:
        assert claude_control.montar_url_remote("") is None


class TestExtrairNovoId:
    def test_acha_id_na_saida_limpa(self) -> None:
        saida = "backgrounded · a1b2c3d4 (idle — send a prompt to start)"
        assert claude_control.extrair_novo_id(saida) == "a1b2c3d4"

    def test_acha_id_com_codigos_ansi(self) -> None:
        saida = "\x1b[32mbackgrounded\x1b[0m · a1b2c3d4 (idle)"
        assert claude_control.extrair_novo_id(saida) == "a1b2c3d4"

    def test_sem_match_retorna_none(self) -> None:
        assert claude_control.extrair_novo_id("saída qualquer sem o padrão esperado") is None


class TestLaunchSession:
    @patch("telegram_claude_remote.claude_control.subprocess.run")
    def test_caminho_feliz(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        ok, session_name, erro = claude_control.launch_session("faz algo")
        assert ok is True
        assert session_name is not None and session_name.startswith("tg-")
        assert erro is None

    @patch("telegram_claude_remote.claude_control.subprocess.run")
    def test_timeout(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="claude", timeout=60)
        ok, session_name, erro = claude_control.launch_session("")
        assert ok is False
        assert session_name is None
        assert erro is not None and "esgotado" in erro

    @patch("telegram_claude_remote.claude_control.subprocess.run")
    def test_returncode_nao_zero(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="deu erro")
        ok, session_name, erro = claude_control.launch_session("")
        assert ok is False
        assert session_name is None
        assert erro is not None and "deu erro" in erro


class TestListSessions:
    @patch("telegram_claude_remote.claude_control.subprocess.run")
    def test_caminho_feliz(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout='[{"id": "abc"}]', stderr="")
        ok, sessoes, erro = claude_control.list_sessions()
        assert ok is True
        assert sessoes == [{"id": "abc"}]
        assert erro is None

    @patch("telegram_claude_remote.claude_control.subprocess.run")
    def test_timeout(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="claude", timeout=20)
        ok, sessoes, erro = claude_control.list_sessions()
        assert ok is False
        assert sessoes is None
        assert erro is not None and "esgotado" in erro

    @patch("telegram_claude_remote.claude_control.subprocess.run")
    def test_returncode_nao_zero(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="falhou")
        ok, sessoes, erro = claude_control.list_sessions()
        assert ok is False
        assert sessoes is None
        assert erro is not None and "falhou" in erro

    @patch("telegram_claude_remote.claude_control.subprocess.run")
    def test_json_invalido(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="não é json", stderr="")
        ok, sessoes, erro = claude_control.list_sessions()
        assert ok is False
        assert sessoes is None
        assert erro is not None and "interpretar" in erro


class TestEncerrarSessao:
    def test_processo_vivo_manda_sigterm(self) -> None:
        with (
            patch(
                "telegram_claude_remote.claude_control.buscar_sessao_por_id",
                return_value={"id": "abc", "pid": 1234},
            ),
            patch("telegram_claude_remote.claude_control.os.kill") as mock_kill,
        ):
            ok, erro = claude_control.encerrar_sessao("abc")
        assert ok is True
        assert erro is None
        mock_kill.assert_called_once_with(1234, signal.SIGTERM)

    def test_sem_permissao_pra_encerrar(self) -> None:
        with (
            patch(
                "telegram_claude_remote.claude_control.buscar_sessao_por_id",
                return_value={"id": "abc", "pid": 1234},
            ),
            patch("telegram_claude_remote.claude_control.os.kill", side_effect=PermissionError),
        ):
            ok, erro = claude_control.encerrar_sessao("abc")
        assert ok is False
        assert erro == "Sem permissão para encerrar essa sessão."

    def test_processo_ja_morto_sem_job_dir_restante(self) -> None:
        with (
            patch(
                "telegram_claude_remote.claude_control.buscar_sessao_por_id",
                return_value={"id": "abc", "pid": 1234},
            ),
            patch("telegram_claude_remote.claude_control.os.kill", side_effect=ProcessLookupError),
            patch("telegram_claude_remote.claude_control.os.path.isdir", return_value=False),
        ):
            ok, erro = claude_control.encerrar_sessao("abc")
        assert ok is False
        assert erro == "Sessão já não existe mais."

    def test_sessao_fantasma_limpa_job_dir(self) -> None:
        with (
            patch("telegram_claude_remote.claude_control.buscar_sessao_por_id", return_value=None),
            patch("telegram_claude_remote.claude_control.os.path.isdir", return_value=True),
            patch("telegram_claude_remote.claude_control.shutil.rmtree") as mock_rmtree,
        ):
            ok, erro = claude_control.encerrar_sessao("fantasma")
        assert ok is True
        assert erro is None
        mock_rmtree.assert_called_once()
