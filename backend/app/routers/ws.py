"""Live agent-trace: WebSocket + a plain REST fallback.

The worker writes each pipeline step to `agent_run_events` AND publishes it to the
Redis channel `rg:events:<return_id>`. On connect we replay the rows already in
the DB, then stream new ones off Redis.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
from uuid import UUID

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy import text

from app.db import Session, get_session
from app.security import Principal, current_user
from app.settings import get_settings

router = APIRouter(tags=["ws"])


async def _existing_events(return_id: str) -> list[dict]:
    async with Session() as s:
        rows = (
            (
                await s.execute(
                    text(
                        "SELECT graph_run_id, seq, agent, kind, payload, "
                        "extract(epoch from created_at)::float8 AS ts "
                        "FROM agent_run_events WHERE return_id = :rid ORDER BY created_at, id"
                    ),
                    {"rid": return_id},
                )
            )
            .mappings()
            .all()
        )
    return [dict(r) for r in rows]


@router.get("/returns/{return_id}/events")
async def events(
    return_id: UUID, p: Principal = Depends(current_user), session=Depends(get_session)
) -> list[dict]:
    await _authorize(str(return_id), p, session)
    return await _existing_events(return_id)


async def _authorize(return_id: str, p: Principal, session) -> None:
    owner = (
        await session.execute(
            text("SELECT user_id::text FROM returns WHERE id=:r"), {"r": return_id}
        )
    ).scalar_one_or_none()
    staff = p.reviewer_id is not None and (p.has("reviewer") or p.has("admin"))
    if owner is None or (owner != p.user_id and not staff):
        raise HTTPException(404, "return not found")


@router.post("/returns/{return_id}/trace-ticket")
async def trace_ticket(
    return_id: UUID, p: Principal = Depends(current_user), session=Depends(get_session)
) -> dict:
    """Exchange server-side authentication for a single-use, case-scoped WS ticket."""
    await _authorize(str(return_id), p, session)
    ticket = secrets.token_urlsafe(32)
    key = "rg:trace-ticket:" + hashlib.sha256(ticket.encode()).hexdigest()
    async with aioredis.from_url(get_settings().redis_url) as r:
        await r.set(key, str(return_id), ex=60)
    return {"ticket": ticket, "expires_in": 60}


@router.websocket("/ws/returns/{return_id}")
async def ws_return_trace(ws: WebSocket, return_id: UUID) -> None:
    return_id = str(return_id)
    ticket = ws.query_params.get("ticket", "")
    if not ticket or len(ticket) > 128:
        await ws.close(code=1008)
        return
    r = aioredis.from_url(get_settings().redis_url)
    key = "rg:trace-ticket:" + hashlib.sha256(ticket.encode()).hexdigest()
    scope = await r.getdel(key)
    if scope is None or scope.decode() != return_id:
        await r.aclose()
        await ws.close(code=1008)
        return
    await ws.accept()
    pubsub = r.pubsub()
    try:
        # Subscribe before replay so an event committed during replay is not lost.
        await pubsub.subscribe(f"rg:events:{return_id}")
        for ev in await _existing_events(return_id):
            await ws.send_json({"replayed": True, **ev})
        while True:
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=20)
            payload = (
                {"heartbeat": True}
                if msg is None
                else {"replayed": False, **json.loads(msg["data"])}
            )
            await ws.send_json(payload)
    except (WebSocketDisconnect, RuntimeError):
        pass
    except Exception:  # noqa: BLE001 — client vanished; nothing to do
        pass
    finally:
        await pubsub.unsubscribe()
        await pubsub.aclose()
        await r.aclose()
        await asyncio.sleep(0)
