"""Bootstrap: carrega .env, entra no loop de long-polling do Telegram."""

import logging
import time

import requests

from . import config, telegram_client
from .config import BotState
from .handlers import process_update

logger = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    env = config.load_env()
    token = env.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN não encontrado em .env")
    state = BotState(owner_chat_id=env.get("OWNER_CHAT_ID") or None)

    base_url = f"https://api.telegram.org/bot{token}"
    offset = config.load_offset()

    logger.info("Bot telegram-claude-remote iniciado. Aguardando mensagens no Telegram...")
    if state.owner_chat_id:
        logger.info("Acesso já travado no chat_id %s.", state.owner_chat_id)
    else:
        logger.info("Sem dono travado ainda — aguardando o primeiro /start.")

    while True:
        try:
            updates = telegram_client.get_updates(base_url, offset)
        except requests.RequestException:
            logger.error("erro telegram", exc_info=True)
            time.sleep(5)
            continue

        for update in updates:
            offset = update["update_id"] + 1
            config.save_offset(offset)
            try:
                process_update(update, base_url, state)
            except Exception:
                logger.exception("erro ao processar update")


if __name__ == "__main__":
    main()
