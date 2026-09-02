from __future__ import annotations

import asyncio
import logging
import os
import re
import signal
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramUnauthorizedError
from aiogram.types import BotCommand, BotCommandScopeChat, BufferedInputFile

from .bioauth import BioauthScheduler
from .catchup import CatchupDetector
from .bioauth_url import DEFAULT_WEBAPP_BASE
from .commands import build_router
from .config import ConfigError, load_config
from .first_sync import FirstSyncWatcher
from .logging import configure_logging
from .node import NodeClient, NodeUnavailable
from .stall import FinalityLagDetector, StallDetector
from .tunnel import NativeTunnel, NgrokTunnel, Tunnel
from .tunnel_watch import TunnelWatcher

logger = logging.getLogger(__name__)

NODE_READY_TIMEOUT_S = 300


def _is_placeholder_token(token: str) -> bool:
    if not token or "REPLACE_ME" in token or "YOUR_BOT_TOKEN" in token:
        return True
    return not re.match(r"^\d+:[A-Za-z0-9_\-]{30,}$", token)


async def _wait_for_node(node: NodeClient, timeout_s: int = NODE_READY_TIMEOUT_S) -> None:
    deadline = asyncio.get_event_loop().time() + timeout_s
    while True:
        try:
            await node.system_health()
            return
        except NodeUnavailable:
            if asyncio.get_event_loop().time() >= deadline:
                raise
            await asyncio.sleep(3)


async def main() -> int:
    try:
        cfg = load_config()
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 1

    redaction = configure_logging()
    redaction.register_many([s for s in [cfg.telegram_bot_token, cfg.ngrok_authtoken] if s])

    logger.info("starting hmnd_bot")

    if _is_placeholder_token(cfg.telegram_bot_token):
        logger.error("TELEGRAM_BOT_TOKEN looks like a placeholder; refusing to start")
        return 1

    bot = Bot(token=cfg.telegram_bot_token, default=DefaultBotProperties(parse_mode=None))
    try:
        me = await bot.get_me()
        logger.info("telegram preflight ok: @%s", me.username)
        try:
            await bot.set_my_commands(
                commands=[
                    BotCommand(command="link", description="Get a fresh bioauth link + QR"),
                    BotCommand(command="reconnect_tunnel", description="Restart tunnel with a new URL"),
                    BotCommand(command="tunnel_status", description="Show tunnel status and URL"),
                    BotCommand(command="cancel_tunnel", description="Close the tunnel"),
                ],
                scope=BotCommandScopeChat(chat_id=cfg.telegram_user_id),
            )
        except Exception:
            logger.exception("set_my_commands failed (non-fatal)")
    except TelegramUnauthorizedError:
        logger.error("Telegram token rejected (401); check TELEGRAM_BOT_TOKEN")
        await bot.session.close()
        return 1
    except Exception:
        logger.exception("Telegram getMe failed")
        await bot.session.close()
        return 1

    node = NodeClient(cfg.rpc_url)
    await node.connect()

    logger.info("waiting for node RPC (budget %ds)", NODE_READY_TIMEOUT_S)
    try:
        await _wait_for_node(node)
    except NodeUnavailable:
        logger.error("node RPC not reachable within %ds", NODE_READY_TIMEOUT_S)
        await node.close()
        await bot.session.close()
        return 1
    logger.info("node RPC reachable")

    tunnel: Tunnel = (
        NgrokTunnel(cfg.ngrok_authtoken, on_url=redaction.register_exact)
        if cfg.ngrok_authtoken
        else NativeTunnel(on_url=redaction.register_exact)
    )
    logger.info("tunnel backend: %s", tunnel.backend)
    webapp_base = os.environ.get("BIOAUTH_WEBAPP_BASE", DEFAULT_WEBAPP_BASE)

    async def send_text(text: str) -> None:
        await bot.send_message(chat_id=cfg.telegram_user_id, text=text)

    async def send_photo(png: bytes, caption: str) -> None:
        await bot.send_photo(
            chat_id=cfg.telegram_user_id,
            photo=BufferedInputFile(png, filename="bioauth.png"),
            caption=caption,
        )

    first_sync = FirstSyncWatcher(node=node, notify=send_text)

    catchup = CatchupDetector(
        node=node,
        max_block_age=cfg.catchup_max_block_age,
        max_block_gap=cfg.catchup_max_block_gap,
        checkpoints=cfg.catchup_checkpoints,
        no_progress_after=cfg.catchup_no_progress_after,
        no_progress_cadence=cfg.catchup_no_progress_remind_after,
    )

    dp = Dispatcher()
    dp.include_router(build_router(
        chat_id=cfg.telegram_user_id,
        node=node,
        tunnel=tunnel,
        catchup=catchup,
        webapp_base=webapp_base,
    ))

    polling_task = asyncio.create_task(
        dp.start_polling(bot, allowed_updates=["message"], handle_signals=False),
        name="telegram-polling",
    )
    first_sync_task = asyncio.create_task(first_sync.run(), name="first-sync")
    catchup_task = asyncio.create_task(catchup.run(), name="catchup")
    tasks = [polling_task, first_sync_task, catchup_task]

    if tunnel.backend == "native":
        tunnel_watch = TunnelWatcher(
            tunnel=tunnel,
            notify=send_text,
            backoff=cfg.tunnel_restart_backoff,
            poll_interval=cfg.tunnel_health_poll,
        )
        tasks.append(asyncio.create_task(tunnel_watch.run(), name="tunnel-health"))

    if cfg.bioauth_remind_before or cfg.bioauth_remind_after:
        bioauth = BioauthScheduler(
            node=node, tunnel=tunnel, catchup=catchup,
            send_photo=send_photo, send_text=send_text,
            remind_before=cfg.bioauth_remind_before,
            remind_after=cfg.bioauth_remind_after,
            webapp_base=webapp_base,
        )
        tasks.append(asyncio.create_task(bioauth.run(), name="bioauth"))

    if cfg.block_stall_threshold and cfg.block_stall_remind_after:
        block_stall = StallDetector(
            name="block", node=node, first_sync=first_sync,
            fetch_block=NodeClient.best_block,
            threshold=cfg.block_stall_threshold,
            remind_cadence=cfg.block_stall_remind_after,
            notify=send_text,
        )
        tasks.append(asyncio.create_task(block_stall.run(), name="block-stall"))

    if cfg.finality_max_lag and cfg.finality_lag_remind_after:
        finality_lag = FinalityLagDetector(
            node=node, first_sync=first_sync,
            max_lag=cfg.finality_max_lag,
            remind_cadence=cfg.finality_lag_remind_after,
            notify=send_text,
        )
        tasks.append(asyncio.create_task(finality_lag.run(), name="finality-lag"))

    stop = asyncio.Event()

    def _request_stop(*_: object) -> None:
        logger.info("shutdown signal received")
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _request_stop)

    stopper = asyncio.create_task(stop.wait(), name="stopper")
    exit_code = 0
    pending = set(tasks + [stopper])
    while pending:
        done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
        shutdown = False
        for t in done:
            if t is stopper:
                shutdown = True
                continue
            if t.exception() is not None:
                logger.error("task %s crashed: %r", t.get_name(), t.exception())
                exit_code = 1
                shutdown = True
            else:
                logger.info("task %s finished cleanly", t.get_name())
        if shutdown:
            break

    for t in tasks:
        if not t.done():
            t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    if not stopper.done():
        stopper.cancel()

    await tunnel.cancel()
    await node.close()
    await bot.session.close()
    logger.info("hmnd_bot exit code=%d", exit_code)
    return exit_code


def run() -> None:
    raise SystemExit(asyncio.run(main()))
