from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, Message

from .bioauth_url import compose_bioauth_url, qr_png_bytes
from .first_sync import FirstSyncWatcher
from .node import NodeClient, NodeUnavailable
from .tunnel import NgrokTunnel, TunnelAuthFailure, TunnelQuotaExceeded, TunnelError

logger = logging.getLogger(__name__)


def build_router(
    *,
    chat_id: int,
    node: NodeClient,
    tunnel: NgrokTunnel,
    first_sync: FirstSyncWatcher,
    webapp_base: str,
) -> Router:
    router = Router()
    router.message.filter(F.chat.id == chat_id)

    @router.message(Command("link"))
    async def handle_link(message: Message) -> None:
        sync_note = ""
        if not first_sync.is_complete:
            try:
                health = await node.system_health()
                best = await node.best_block()
                sync_note = (f"⏳ Still syncing (best block #{best.number}, peers {health.peers}). "
                             f"You can open the link now; bioauth will complete once the chain catches up.\n")
            except NodeUnavailable:
                await message.answer("Node RPC is unreachable right now. Try again shortly.")
                return

        try:
            wss_url = await tunnel.start()
        except TunnelAuthFailure:
            await message.answer("ngrok authtoken rejected. Check NGROK_AUTHTOKEN and restart.")
            return
        except TunnelQuotaExceeded:
            await message.answer("ngrok quota exceeded. Upgrade plan or /cancel_tunnel and retry.")
            return
        except TunnelError as e:
            await message.answer(f"Tunnel failure: {e}")
            return

        bioauth_url = compose_bioauth_url(wss_url, webapp_base=webapp_base)
        png = qr_png_bytes(bioauth_url)
        await message.answer_photo(
            photo=BufferedInputFile(png, filename="bioauth.png"),
            caption=sync_note + bioauth_url,
        )

    @router.message(Command("cancel_tunnel", "cancel-tunnel"))
    async def handle_cancel_tunnel(message: Message) -> None:
        await tunnel.cancel()
        await message.answer("Tunnel closed. Next /link will open a fresh one.")

    @router.message(Command("reconnect_tunnel", "reconnect-tunnel"))
    async def handle_reconnect_tunnel(message: Message) -> None:
        await message.answer("Restarting tunnel...")
        try:
            wss_url = await tunnel.reconnect()
        except TunnelError as e:
            await message.answer(f"Tunnel reconnect failed: {e}")
            return
        bioauth_url = compose_bioauth_url(wss_url, webapp_base=webapp_base)
        png = qr_png_bytes(bioauth_url)
        await message.answer_photo(
            photo=BufferedInputFile(png, filename="bioauth.png"),
            caption=f"Tunnel reconnected.\n{bioauth_url}",
        )

    @router.message(Command("tunnel_status", "tunnel-status"))
    async def handle_tunnel_status(message: Message) -> None:
        live_url = await tunnel.refresh_url()
        if live_url:
            await message.answer(f"Tunnel: active\nURL: {live_url}")
        elif tunnel.is_running():
            await message.answer(
                "Tunnel: service running but no URL detected.\n"
                "The relay may have rejected the connection. "
                "The service is retrying automatically."
            )
        else:
            await message.answer("Tunnel: not running. Use /link to start.")

    return router
