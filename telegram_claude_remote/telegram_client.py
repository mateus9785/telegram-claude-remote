"""Cliente HTTP mínimo pra Telegram Bot API (mensagens, callbacks, long-polling)."""

import requests

from . import config


def enviar_mensagem(base_url, chat_id, texto, reply_markup=None):
    payload = {"chat_id": chat_id, "text": texto}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        requests.post(f"{base_url}/sendMessage", json=payload, timeout=15)
    except requests.RequestException as e:
        print(f"[erro ao enviar para {chat_id}] {e}")


def responder_callback(base_url, callback_query_id, texto=None, show_alert=False):
    payload = {"callback_query_id": callback_query_id}
    if texto:
        payload["text"] = texto
    if show_alert:
        payload["show_alert"] = True
    try:
        requests.post(f"{base_url}/answerCallbackQuery", json=payload, timeout=15)
    except requests.RequestException as e:
        print(f"[erro ao responder callback] {e}")


def get_updates(base_url, offset):
    """Faz o long-poll `getUpdates` e devolve a lista de updates. Deixa
    requests.RequestException propagar pro chamador decidir o que fazer
    (main() espera 5s e tenta de novo)."""
    params = {"timeout": config.POLL_TIMEOUT}
    if offset is not None:
        params["offset"] = offset

    resp = requests.get(f"{base_url}/getUpdates", params=params, timeout=config.POLL_TIMEOUT + 10)
    resp.raise_for_status()
    return resp.json().get("result", [])
