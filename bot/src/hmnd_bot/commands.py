from __future__ import annotations

import logging
from datetime import timedelta

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, Message

from .bioauth_url import compose_bioauth_url, qr_png_bytes
from .catchup import CatchupDetector
from .node import NodeClient, NodeUnavailable
from .tunnel import Tunnel, TunnelAuthFailure, TunnelQuotaExceeded, TunnelError, TunnelState

logger = logging.getLogger(__name__)


def build_router(
    *,
    chat_id: int,
    node: NodeClient,
    tunnel: Tunnel,
    catchup: CatchupDetector,
    webapp_base: str,
) -> Router:
    router = Router()
    router.message.filter(F.chat.id == chat_id)

    @router.message(Command("link"))
    async def handle_link(message: Message) -> None:
        if catchup.is_behind:
            try:
                health = await node.system_health()
                best = await node.best_block()
            except NodeUnavailable:
                await message.answer("Node RPC is unreachable right now. Try again shortly.")
                return
            await message.answer(_syncing_reply(catchup.gap, best.number, health.peers, catchup.eta))
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
            caption=bioauth_url,
        )

    @router.message(Command("cancel_tunnel", "cancel-tunnel"))
    async def handle_cancel_tunnel(message: Message) -> None:
        if not tunnel.supports_cancel:
            await message.answer(
                "The native tunnel is a supervised service and cannot be closed from here. "
                "Use /reconnect_tunnel to restart it."
            )
            return
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
        state = await tunnel.state()
        if state is TunnelState.CONNECTED:
            await message.answer(f"Tunnel ({tunnel.backend}): active\nURL: {tunnel.url()}")
        elif state is TunnelState.CONNECTING:
            await message.answer(
                f"Tunnel ({tunnel.backend}): running but not connected to the relay.\n"
                "It retries automatically; /reconnect_tunnel forces a restart."
            )
        else:
            await message.answer(
                f"Tunnel ({tunnel.backend}): not running.\n"
                "Use /reconnect_tunnel to restart the service."
            )

    return router


def _syncing_reply(gap: int | None, best_block: int, peers: int, eta: timedelta | None = None) -> str:
    behind = _blocks(gap)
    eta_part = f", ETA ~{_human(eta)}" if eta is not None else ""
    return (f"⏳ Node is still syncing ({behind} behind, best block #{best_block}, peers {peers}{eta_part}). "
            f"Wait for sync to complete before attempting a facescan.")

def _blocks(gap: int | None) -> str:
    if gap is None:
        return "unknown"
    if gap >= 1_000_000:
        return f"{gap / 1_000_000:.1f}M blocks"
    if gap >= 1_000:
        return f"{gap / 1_000:.1f}K blocks"
    return f"{gap} blocks"

def _human(d: timedelta) -> str:
    total = int(d.total_seconds())
    if total < 60:
        return f"{total}s"
    if total < 3600:
        return f"{total // 60}m"
    if total < 86400:
        return f"{total // 3600}h {(total % 3600) // 60}m"
    return f"{total // 86400}d {(total % 86400) // 3600}h"
