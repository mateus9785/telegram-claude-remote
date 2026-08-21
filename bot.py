#!/usr/bin/env python3
"""Entrypoint fino — a lógica real mora em telegram_claude_remote/.
Mantido na raiz porque é o que o `pm2 start bot.py` já roda em produção."""

from telegram_claude_remote.main import main

if __name__ == "__main__":
    main()
