"""Exercise the real governance gate and settlement job with explicit input fixtures.

Runs inside the lightweight worker image against Postgres, Redis, Vault, and OPA.
No LLM responses are fabricated and no hosted model calls are made. The supplied
signals test the deterministic governance boundary, not the quality of AI decisions.
"""

from __future__ import annotations

import asyncio
import uuid

from pipeline import audit, db, governance, jobs, models_config, opa

SCOPE = "verification-" + uuid.uuid4().hex[:12]
CASES: list[str] = []


def check(condition, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print("PASS: " + message, flush=True)


async def case(amount=12, **signals) -> dict:
    product = await db.fetchrow("SELECT id, name FROM products ORDER BY sku LIMIT 1")
    if product is None:
        raise RuntimeError("run the real catalog seed first")
    uid = await db.fetchval(
        "INSERT INTO users(keycloak_sub,email,name,role) VALUES (%(s)s,%(e)s,%(n)s,'customer') RETURNING id",
        {
            "s": str(uuid.uuid4()),
            "e": uuid.uuid4().hex + "@worker-verification.local",
            "n": SCOPE,
        },
    )
    oid = await db.fetchval(
        "INSERT INTO orders(user_id,status,total,shipping_address,payment_last4) "
        "VALUES (%(u)s,'placed',%(a)s,'{}','4242') RETURNING id",
        {"u": uid, "a": amount},
    )
    item = await db.fetchval(
        "INSERT INTO order_items(order_id,product_id,name_snapshot,unit_price,qty) "
        "VALUES (%(o)s,%(p)s,%(n)s,%(a)s,1) RETURNING id",
        {"o": oid, "p": product["id"], "n": product["name"], "a": amount},
    )
    graph = uuid.uuid4().hex
    rid = await db.fetchval(
        "INSERT INTO returns(order_id,order_item_id,user_id,reason_code,reason_text,status,refund_state,amount,graph_run_id) "
        "VALUES (%(o)s,%(i)s,%(u)s,'no_longer_needed',%(n)s,'in_review','none',%(a)s,%(g)s) RETURNING id::text",
        {"o": oid, "i": item, "u": uid, "a": amount, "g": graph, "n": SCOPE},
    )
    CASES.append(rid)
    state = {
        "return_id": rid,
        "graph_run_id": graph,
        "automation_level": "auto",
        "category": SCOPE,
        "context": {"amount": amount},
        "policy": {"high_value_flag": False},
        "data_quality": {"ok": True},
        "critic": {"veto": False},
        "decision": {"decision": "approve", "confidence": 0.95, "risk": 0.0},
        "explanation": "Explicit local governance boundary verification inputs.",
        "seq": 0,
    }
    state.update(signals)
    return state


async def main() -> None:
    await db.start()
    original = await db.fetchrow("SELECT * FROM feature_flags WHERE scope='global'")
    try:
        await db.execute(
            "INSERT INTO feature_flags(scope,automation_level,kill_switch,qa_sample_pct,tau_risk,tau_conf) "
            "VALUES (%(s)s,'auto',false,0,0.3,0.8)",
            {"s": SCOPE},
        )
        await db.execute(
            "UPDATE feature_flags SET kill_switch=true WHERE scope='global'"
        )
        check(
            (await db.current_flags(SCOPE))["kill_switch"],
            "global kill switch dominates category auto",
        )
        state = await case()
        final = (await governance.run(state))["final"]
        check(
            final["route"] == "escalate",
            "live OPA gate escalates despite category override",
        )
        await db.execute(
            "UPDATE feature_flags SET kill_switch=false WHERE scope='global'"
        )
        clean = await case()
        final = (await governance.run(clean))["final"]
        check(
            final["route"] == "auto_approve" and final["risk"] == 0,
            "a legitimate zero risk score is preserved",
        )
        await jobs.process_refunds({})
        check(
            await db.fetchval(
                "SELECT refund_state FROM returns WHERE id=%(r)s",
                {"r": clean["return_id"]},
            )
            == "pending",
            "an agent approval alone cannot settle a refund",
        )

        await db.execute(
            "UPDATE feature_flags SET automation_level='assist', qa_sample_pct=100 WHERE scope=%(s)s",
            {"s": SCOPE},
        )
        sampled = await case()
        final = (await governance.run(sampled))["final"]
        check(
            final["qa_sample"] and final["route"] == "escalate" and not final["auto"],
            "100% QA routes to a human before finalization",
        )
        check(
            await db.fetchval(
                "SELECT count(*) FROM agreement_samples WHERE return_id=%(r)s",
                {"r": sampled["return_id"]},
            )
            == 0,
            "an unreviewed QA case does not invent an agreement sample",
        )
        await db.execute(
            "UPDATE feature_flags SET automation_level='auto', qa_sample_pct=0 WHERE scope=%(s)s",
            {"s": SCOPE},
        )
        for label, state in (
            (
                "proposed denial",
                await case(
                    decision={"decision": "deny", "confidence": 0.99, "risk": 0}
                ),
            ),
            ("high value despite a missing model flag", await case(amount=329)),
            ("critic veto", await case(critic={"veto": True})),
            ("missing data-quality verdict", await case(data_quality={})),
        ):
            check(
                (await governance.run(state))["final"]["route"] == "escalate",
                label + " requires human review",
            )
        waiting = await case()
        await db.execute(
            "UPDATE returns SET status='info_requested' WHERE id=%(r)s",
            {"r": waiting["return_id"]},
        )
        check(
            "skipped" in await jobs.review_return({}, waiting["return_id"]),
            "unanswered information requests do not run the pipeline",
        )

        probe = await case()
        before = await db.fetchval("SELECT count(*) FROM audit_log")
        try:
            async with db.pool.connection() as conn, conn.transaction():
                await conn.execute(
                    "UPDATE returns SET status='escalated' WHERE id=%(r)s",
                    {"r": probe["return_id"]},
                )
                await audit.append(
                    conn=conn,
                    actor_type="system",
                    actor_id=SCOPE,
                    action="rollback_probe",
                    entity_type="return",
                    entity_id=probe["return_id"],
                    data={},
                )
                raise ValueError("deliberate rollback after both real writes")
        except ValueError:
            pass
        check(
            await db.fetchval(
                "SELECT status FROM returns WHERE id=%(r)s", {"r": probe["return_id"]}
            )
            == "in_review"
            and await db.fetchval("SELECT count(*) FROM audit_log") == before,
            "state and audit roll back together",
        )

        original_url = opa._URL
        try:
            opa._URL = "http://127.0.0.1:1"
            check(
                not models_config.is_granted("policy", "reason"),
                "a real OPA connection failure denies model access",
            )
            check(
                (await governance.run(await case()))["final"]["route"] == "escalate",
                "a real OPA outage fails closed at the gate",
            )
        finally:
            opa._URL = original_url
        print("verify_worker_local: all checks passed", flush=True)
    finally:
        if original:
            await db.execute(
                "UPDATE feature_flags SET kill_switch=%(k)s WHERE scope='global'",
                {"k": original["kill_switch"]},
            )
        await db.execute("DELETE FROM feature_flags WHERE scope=%(s)s", {"s": SCOPE})
        # Keep the evidence rows; leave no unprocessed case eligible for a future worker.
        for rid in CASES:
            await db.execute(
                "UPDATE returns SET status='escalated' WHERE id=%(r)s AND status IN ('in_review','pending','info_requested')",
                {"r": rid},
            )
        await db.stop()


if __name__ == "__main__":
    asyncio.run(main())
