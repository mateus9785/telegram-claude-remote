"""Bootstrap: carrega .env, entra no loop de long-polling do Telegram."""

import time

import requests

from . import config, telegram_client
from .config import BotState
from .handlers import process_update


def main():
    env = config.load_env()
    token = env.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN não encontrado em .env")
    state = BotState(owner_chat_id=env.get("OWNER_CHAT_ID") or None)

    base_url = f"https://api.telegram.org/bot{token}"
    offset = config.load_offset()

    print("Bot telegram-claude-remote iniciado. Aguardando mensagens no Telegram...")
    if state.owner_chat_id:
        print(f"Acesso já travado no chat_id {state.owner_chat_id}.")
    else:
        print("Sem dono travado ainda — aguardando o primeiro /start.")

    while True:
        try:
            updates = telegram_client.get_updates(base_url, offset)
        except requests.RequestException as e:
            print(f"[erro telegram] {e}")
            time.sleep(5)
            continue

        for update in updates:
            offset = update["update_id"] + 1
            config.save_offset(offset)
            try:
                process_update(update, base_url, state)
            except Exception as e:
                print(f"[erro ao processar update] {e}")


if __name__ == "__main__":
    main()
