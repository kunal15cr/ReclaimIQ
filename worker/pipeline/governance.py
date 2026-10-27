"""GovernanceGate — the ONLY place a decision is finalized.

The decision itself (auto_approve vs escalate + the human-readable reasons) is a
policy, evaluated by **OPA** — `infra/opa/governance.rego`. This module builds the
input from pipeline state, asks OPA, then owns the *effects*: the QA-sampling
dice roll and the `returns` + append-only `audit_log` writes.

The rules OPA enforces (see the .rego):
  * kill switch on            -> escalate (every case)
  * data-quality failed       -> escalate ("insufficient data")
  * Decision agent said deny   -> escalate (proposed=deny)   [auto-deny never ships]
  * Policy high-value flag     -> escalate
  * Critic veto                -> escalate
  * auto-approve ONLY IF risk < tau_risk AND confidence > tau_conf
                 AND automation_level in (assist, auto)
  * everything else            -> escalate
If OPA is unreachable the gate fails closed (escalate).
"""

from __future__ import annotations

import random

import structlog

from pipeline import audit, db, opa
from pipeline.nodes.base import RunCtx, record_local_agent

log = structlog.get_logger()


async def _flags(category: str | None) -> dict:
    return await db.current_flags(category)


async def run(state: dict) -> dict:
    rc = RunCtx(
        state["return_id"],
        state["graph_run_id"],
        state["automation_level"],
        state.get("policy", {}).get("policy_version"),
    )
    f = await _flags(state.get("category"))
    level = f["automation_level"]
    tau_risk, tau_conf = float(f["tau_risk"]), float(f["tau_conf"])

    dec = state.get("decision", {}) or {}
    proposed = dec.get("decision", "escalate")
    conf = float(dec.get("confidence", 0) or 0)
    risk = float(dec["risk"]) if dec.get("risk") is not None else 1.0
    dq = state.get("data_quality", {}) or {}
    dq_ok = dq.get("ok", False)

    opa_input = {
        "kill_switch": bool(f["kill_switch"]),
        "dq_ok": bool(dq_ok),
        "dq_reason": dq.get("reason") or "",
        "proposed": proposed,
        "high_value": (
            bool(state.get("policy", {}).get("high_value_flag"))
            or float(state.get("context", {}).get("amount", 251)) > 250
        ),
        "critic_veto": bool(state.get("critic", {}).get("veto")),
        "critic_concern": state.get("critic", {}).get("concern") or "",
        "risk": risk,
        "confidence": conf,
        "tau_risk": tau_risk,
        "tau_conf": tau_conf,
        "automation_level": level,
    }
    verdict = opa.query("returnguard/governance/decision", opa_input)
    engine = "opa"
    if verdict is None:  # fail closed
        engine = "fallback"
        verdict = {
            "route": "escalate",
            "reasons": ["policy engine (OPA) unavailable — routed to a human"],
        }
    route = verdict.get("route", "escalate")
    reasons = list(verdict.get("reasons") or [])
    if not dq_ok:
        proposed = "escalate"

    qa_sample = False
    if (
        route == "auto_approve"
        and level == "assist"
        and random.random() < f["qa_sample_pct"] / 100
    ):
        qa_sample = True
        reasons.append(
            f"QA sample: 1-in-{max(1, round(100 / max(f['qa_sample_pct'], 1)))} auto-approvals reviewed"
        )
        route = "escalate"

    final = {
        "route": route,  # auto_approve | escalate
        "proposed": proposed,  # approve | deny | escalate
        "automation_level": level,
        "kill_switch": bool(f["kill_switch"]),
        "auto": route == "auto_approve",
        "qa_sample": qa_sample,
        "reason": "; ".join(reasons) or "routed to a human by policy",
        "policy_engine": engine,  # opa | fallback
        "tau_risk": tau_risk,
        "tau_conf": tau_conf,
        "risk": risk,
        "confidence": conf,
    }
    await record_local_agent(
        rc,
        state,
        agent="governance",
        output=final,
        model=f"governance_gate:{engine}",
        latency_ms=0,
    )
    await _apply(rc, state, final)
    await rc.emit(state, "node_end", "governance", final)
    return {"final": final}


async def _apply(rc: RunCtx, state: dict, final: dict) -> None:
    async with db.pool.connection() as conn:
        async with conn.transaction():
            await _apply_in_transaction(conn, state, final)


async def _apply_in_transaction(conn, state: dict, final: dict) -> None:
    rid = state["return_id"]
    cur = await conn.execute(
        "SELECT status, graph_run_id FROM returns WHERE id=%(r)s FOR UPDATE", {"r": rid}
    )
    row = await cur.fetchone()
    if row is None or row[0] != "in_review" or row[1] != state["graph_run_id"]:
        raise RuntimeError("pipeline no longer owns this return")
    explanation = state.get("explanation") or state.get("decision", {}).get(
        "reason", ""
    )

    if final["route"] == "auto_approve":
        await conn.execute(
            "UPDATE returns SET status='approved', decision=%(d)s, final_decision='approve', "
            "decision_reason=%(r)s, refund_state='pending', decided_at=now() "
            "WHERE id=%(id)s",
            {"d": final["proposed"], "r": explanation, "id": rid},
        )
        rh = await audit.append(
            actor_type="system",
            actor_id="governance_gate",
            action="auto_approve",
            entity_type="return",
            entity_id=rid,
            conn=conn,
            data={
                "final": final,
                "decision": state.get("decision"),
                "graph_run_id": state["graph_run_id"],
            },
        )
        log.info("governance.auto_approve", return_id=rid, audit=rh[:12])
    else:
        await conn.execute(
            "UPDATE returns SET status='escalated', decision=%(d)s, decision_reason=%(r)s "
            "WHERE id=%(id)s",
            {"d": final["proposed"], "r": explanation, "id": rid},
        )
        await audit.append(
            actor_type="system",
            actor_id="governance_gate",
            action="escalate",
            entity_type="return",
            entity_id=rid,
            conn=conn,
            data={
                "final": final,
                "decision": state.get("decision"),
                "proposed": final["proposed"],
                "graph_run_id": state["graph_run_id"],
            },
        )
        log.info("governance.escalate", return_id=rid, proposed=final["proposed"])
