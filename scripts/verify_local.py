"""Verify local workflows over real HTTP, Keycloak, Redis, and Postgres.

No model calls are made. Cases are explicitly staged into human-review states
with SQL to exercise authorization and state transitions, without inventing an
agent proposal or claiming that an AI pipeline ran. Run with the worker stopped.
Requires migrations, seeded products, and the backend/identity/storage services.
"""

from __future__ import annotations

import asyncio
import secrets
import uuid

import websockets
from _kc import KC, REALM, admin_token, register_user, token
from _rg import API, Check, db, httpx, q1

ACCOUNTS: list[tuple[str, str]] = []


def account(role: str) -> dict:
    email = f"local-{role}-{uuid.uuid4().hex[:10]}@test.local"
    password = secrets.token_urlsafe(24)
    uid = register_user(email, password, roles=[role])
    ACCOUNTS.append((uid, email))
    h = {"Authorization": f"Bearer {token(email, password)}"}
    httpx.get(f"{API}/orders", headers=h, timeout=15).raise_for_status()
    return h


def cleanup() -> None:
    with db() as conn:
        conn.execute(
            "UPDATE reviewers SET active=false WHERE user_id IN "
            "(SELECT id FROM users WHERE email=ANY(%(emails)s))",
            {"emails": [email for _, email in ACCOUNTS]},
        )
    h = {"Authorization": f"Bearer {admin_token()}"}
    for uid, _ in ACCOUNTS:
        httpx.put(
            f"{KC}/admin/realms/{REALM}/users/{uid}",
            headers=h,
            json={"enabled": False},
            timeout=15,
        ).raise_for_status()


def request(method: str, path: str, headers=None, **kwargs):
    return httpx.request(method, f"{API}{path}", headers=headers, timeout=20, **kwargs)


def checkout(h: dict) -> str:
    products = request("GET", "/products").json()
    response = request(
        "POST",
        "/orders",
        h,
        json={
            "lines": [{"product_id": products[0]["id"], "qty": 1}],
            "shipping_address": {
                "line1": "1 Local Verification St",
                "city": "Test",
                "zip": "00001",
            },
            "card_number": "4242424242424242",
            "card_exp": "12/30",
            "card_cvc": "123",
        },
    )
    response.raise_for_status()
    return response.json()["items"][0]["id"]


def new_return(h: dict) -> str:
    response = request(
        "POST",
        "/returns",
        h,
        data={
            "order_item_id": checkout(h),
            "reason_code": "no_longer_needed",
            "reason_text": "Unopened.",
        },
    )
    response.raise_for_status()
    return response.json()["id"]


def stage(rid: str, status: str = "escalated") -> None:
    # Explicit workflow fixture, not a fabricated agent result.
    with db() as conn:
        conn.execute(
            "UPDATE returns SET status=%(s)s WHERE id=%(r)s", {"s": status, "r": rid}
        )


async def socket_status(rid: str, ticket: str = "") -> bool:
    try:
        async with websockets.connect(
            f"ws://localhost:8000/ws/returns/{rid}?ticket={ticket}",
            open_timeout=10,
        ) as ws:
            await ws.ping()
            return True
    except websockets.exceptions.InvalidStatus:
        return False


def main() -> None:
    c = Check("verify_local")
    customer, outsider = account("customer"), account("customer")
    rev, rev2, admin = account("reviewer"), account("reviewer"), account("admin")
    rid = new_return(customer)
    c.ok(
        q1("SELECT count(*) FROM return_photos WHERE return_id=%(r)s", r=rid) == 0,
        "a non-visual return accepts no photo",
    )
    c.ok(
        request(
            "POST",
            "/returns",
            customer,
            data={
                "order_item_id": checkout(customer),
                "reason_code": "damaged",
            },
        ).status_code
        == 422,
        "damage requires a real photo",
    )
    c.ok(
        request("GET", f"/returns/{rid}/events").status_code == 401,
        "trace REST requires login",
    )
    c.ok(
        request("GET", f"/returns/{rid}/events", outsider).status_code == 404,
        "another customer cannot read case traces",
    )
    c.ok(
        request("POST", f"/returns/{rid}/trace-ticket", outsider).status_code == 404,
        "another customer cannot obtain a trace ticket",
    )
    c.ok(
        not asyncio.run(socket_status(rid)), "WebSocket rejects unauthenticated access"
    )
    ticket = request("POST", f"/returns/{rid}/trace-ticket", customer).json()["ticket"]
    c.ok(
        asyncio.run(socket_status(rid, ticket)),
        "authorized single-use ticket opens WebSocket",
    )
    c.ok(
        not asyncio.run(socket_status(rid, ticket)),
        "a consumed ticket cannot be reused",
    )
    c.ok(
        request(
            "POST",
            f"/dashboard/returns/{rid}/decide",
            rev,
            json={"decision": "approve"},
        ).status_code
        == 409,
        "a reviewer cannot finalize a pending pipeline case",
    )
    stage(rid)
    request(
        "POST",
        f"/dashboard/returns/{rid}/request-info",
        rev,
        json={"question": "Is the package still sealed?"},
    ).raise_for_status()
    c.ok(
        request(
            "POST",
            f"/dashboard/returns/{rid}/request-info",
            rev,
            json={"question": "Duplicate?"},
        ).status_code
        == 409,
        "a second unanswered information request is rejected",
    )
    c.ok(
        request(
            "POST", f"/returns/{rid}/info-request", customer, data={"answer": " "}
        ).status_code
        == 422,
        "blank answers are rejected",
    )
    before = q1("SELECT count(*) FROM outbox WHERE payload->>'return_id'=%(r)s", r=rid)
    request(
        "POST",
        f"/returns/{rid}/info-request",
        customer,
        data={"answer": "Yes, still sealed."},
    ).raise_for_status()
    c.ok(
        q1("SELECT status FROM returns WHERE id=%(r)s", r=rid) == "pending",
        "an answer requeues the case",
    )
    c.ok(
        q1("SELECT count(*) FROM outbox WHERE payload->>'return_id'=%(r)s", r=rid)
        == before + 1,
        "an answer writes a durable outbox event",
    )
    c.ok(
        q1(
            "SELECT count(*) FROM audit_log WHERE entity_id=%(r)s AND action='answer_info'",
            r=rid,
        )
        == 1,
        "the answer is audited",
    )
    stage(rid)
    c.ok(
        request(
            "POST", f"/dashboard/returns/{rid}/decide", rev, json={"decision": "deny"}
        ).status_code
        == 400,
        "denial requires confirmation",
    )
    request(
        "POST",
        f"/dashboard/returns/{rid}/decide",
        rev,
        json={"decision": "deny", "confirm": True},
    ).raise_for_status()
    c.ok(
        request(
            "POST",
            f"/dashboard/returns/{rid}/request-info",
            rev2,
            json={"question": "Reopen?"},
        ).status_code
        == 409,
        "a decided case cannot be reopened by requesting information",
    )
    request(
        "POST",
        f"/appeals/returns/{rid}",
        customer,
        json={"reason": "Please reconsider; it is unopened."},
    ).raise_for_status()
    appeal_id, assigned = q1(
        "SELECT id::text, assigned_to::text FROM appeals WHERE return_id=%(r)s", r=rid
    )
    assigned_h = next(
        h
        for h in (rev2, admin)
        if q1(
            "SELECT rv.id::text FROM reviewers rv JOIN users u ON u.id=rv.user_id "
            "WHERE u.keycloak_sub=%(sub)s",
            sub=__import__("jwt").decode(
                h["Authorization"].split()[1], options={"verify_signature": False}
            )["sub"],
        )
        == assigned
    )
    c.ok(
        request("POST", f"/dashboard/returns/{rid}/claim", rev).status_code == 403,
        "the original reviewer cannot claim their appeal",
    )
    c.ok(
        request(
            "POST", f"/appeals/{appeal_id}/resolve?outcome=approve", rev
        ).status_code
        == 403,
        "the original reviewer cannot resolve their appeal",
    )
    c.ok(
        request(
            "POST",
            f"/dashboard/returns/{rid}/decide",
            assigned_h,
            json={"decision": "approve"},
        ).status_code
        == 409,
        "ordinary case actions cannot bypass an open appeal",
    )
    c.ok(
        request(
            "POST", f"/appeals/{appeal_id}/resolve?outcome=deny", assigned_h
        ).status_code
        == 400,
        "appeal denial requires server-side confirmation",
    )
    request(
        "POST", f"/appeals/{appeal_id}/resolve?outcome=deny&confirm=true", assigned_h
    ).raise_for_status()
    c.ok(
        request(
            "POST", f"/appeals/{appeal_id}/resolve?outcome=approve", assigned_h
        ).status_code
        == 409,
        "a resolved appeal cannot be decided again",
    )
    for patch in (
        {"tau_risk": -1},
        {"tau_conf": 2},
        {"qa_sample_pct": 101},
        {"automation_level": "unbounded"},
    ):
        c.ok(
            request(
                "PUT", "/dashboard/governance", admin, json={"scope": "global", **patch}
            ).status_code
            == 422,
            f"invalid governance setting rejected: {next(iter(patch))}",
        )
    refund_case = new_return(customer)
    with db() as conn:
        conn.execute(
            "UPDATE returns SET status='approved', final_decision='approve', refund_state='pending' "
            "WHERE id=%(r)s",
            {"r": refund_case},
        )
    c.ok(
        request(
            "POST", f"/dashboard/returns/{refund_case}/refund", customer
        ).status_code
        == 403,
        "a customer cannot authorize settlement",
    )
    request("POST", f"/dashboard/returns/{refund_case}/refund", rev2).raise_for_status()
    c.ok(
        q1("SELECT decided_by IS NOT NULL FROM returns WHERE id=%(r)s", r=refund_case),
        "refund authorization records the human reviewer",
    )
    c.ok(
        request("POST", f"/dashboard/returns/{refund_case}/refund", rev2).status_code
        == 409,
        "refund authorization cannot be repeated",
    )
    c.done()


if __name__ == "__main__":
    try:
        main()
    finally:
        cleanup()
