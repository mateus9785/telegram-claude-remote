"""Cliente HTTP mínimo pra Telegram Bot API (mensagens, callbacks, long-polling)."""

import logging
from typing import Any

import requests

from . import config

logger = logging.getLogger(__name__)


def enviar_mensagem(base_url: str, chat_id: int | str, texto: str, reply_markup: dict[str, Any] | None = None) -> None:
    payload: dict[str, Any] = {"chat_id": chat_id, "text": texto}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        requests.post(f"{base_url}/sendMessage", json=payload, timeout=15)
    except requests.RequestException:
        logger.error("erro ao enviar para %s", chat_id, exc_info=True)


def responder_callback(
    base_url: str, callback_query_id: str, texto: str | None = None, show_alert: bool = False
) -> None:
    payload: dict[str, Any] = {"callback_query_id": callback_query_id}
    if texto:
        payload["text"] = texto
    if show_alert:
        payload["show_alert"] = True
    try:
        requests.post(f"{base_url}/answerCallbackQuery", json=payload, timeout=15)
    except requests.RequestException:
        logger.error("erro ao responder callback", exc_info=True)


def get_updates(base_url: str, offset: int | None) -> list[dict[str, Any]]:
    """Faz o long-poll `getUpdates` e devolve a lista de updates. Deixa
    requests.RequestException propagar pro chamador decidir o que fazer
    (main() espera 5s e tenta de novo)."""
    params = {"timeout": config.POLL_TIMEOUT}
    if offset is not None:
        params["offset"] = offset

    resp = requests.get(f"{base_url}/getUpdates", params=params, timeout=config.POLL_TIMEOUT + 10)
    resp.raise_for_status()
    return resp.json().get("result", [])
