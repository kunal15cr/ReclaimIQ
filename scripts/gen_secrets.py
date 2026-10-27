"""One-shot secret generator for a clean checkout.

Creates `.env` from `.env.example` and `frontend/.env.local` from its example,
filling every generated-credential field with a fresh random value. Idempotent:
a field that already has a non-empty value is left untouched, so re-running never
rotates generated credentials. The frontend client credential is synchronized
from the bootstrap value. The two paid API keys are never touched — you fill
those in by hand.

    python scripts/gen_secrets.py

Shapes that matter:
  * LANGFUSE_ENCRYPTION_KEY  — exactly 64 hex chars (AES-256 key)
  * LANGFUSE_INIT_PROJECT_*  — pk-lf-… / sk-lf-… (Langfuse convention)
  * everything else          — 32-char url-safe token
"""

from __future__ import annotations

import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# field -> how to mint it. Anything not listed is copied through unchanged.
GEN = {
    "POSTGRES_PASSWORD": lambda: secrets.token_urlsafe(24),
    "MINIO_ROOT_PASSWORD": lambda: secrets.token_urlsafe(24),
    "KEYCLOAK_ADMIN_PASSWORD": lambda: secrets.token_urlsafe(18),
    "LANGFUSE_SALT": lambda: secrets.token_urlsafe(24),
    "LANGFUSE_NEXTAUTH_SECRET": lambda: secrets.token_urlsafe(32),
    "NEXTAUTH_SECRET": lambda: secrets.token_urlsafe(32),
    "CONTEXTFORGE_JWT_SECRET": lambda: secrets.token_urlsafe(32),
    "BIFROST_ADMIN_TOKEN": lambda: secrets.token_urlsafe(32),
    "LANGFUSE_ENCRYPTION_KEY": lambda: secrets.token_hex(32),
    "LANGFUSE_INIT_PROJECT_SECRET_KEY": lambda: "sk-lf-" + secrets.token_urlsafe(32),
    "LANGFUSE_INIT_PROJECT_PUBLIC_KEY": lambda: "pk-lf-" + secrets.token_urlsafe(32),
}
API_KEYS = ("OPENAI_API_KEY", "GROQ_API_KEY")
CLIENT_SECRETS = {
    "returnguard-web": "KEYCLOAK_SECRET_WEB",
    "returnguard-backend": "KEYCLOAK_SECRET_BACKEND",
    **{
        f"returnguard-agent-{agent}": f"KEYCLOAK_SECRET_{agent.upper()}"
        for agent in (
            "planner",
            "intake",
            "policy",
            "image",
            "behavior",
            "decision",
            "critic",
            "explanation",
        )
    },
}
GEN.update({key: lambda: secrets.token_urlsafe(32) for key in CLIENT_SECRETS.values()})


def _fill(
    example: Path, target: Path, overrides: dict | None = None
) -> tuple[int, int]:
    """Write `target` from `example`, generating blanks. Returns (generated, kept)."""
    src = target if target.exists() else example
    generated = kept = 0
    out: list[str] = []
    lines = src.read_text(encoding="utf-8").splitlines()
    existing = {
        line.partition("=")[0].strip()
        for line in lines
        if "=" in line and not line.startswith("#")
    }
    if target.exists():
        lines.extend(
            line
            for line in example.read_text(encoding="utf-8").splitlines()
            if "=" in line
            and not line.startswith("#")
            and line.partition("=")[0].strip() not in existing
        )
    for line in lines:
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            out.append(line)
            continue
        key, _, val = s.partition("=")
        key, val = key.strip(), val.strip()
        if overrides and key in overrides:
            val = overrides[key]
        if val:
            kept += 1
            out.append(f"{key}={val}")
        elif key in GEN:
            generated += 1
            out.append(f"{key}={GEN[key]()}")
        else:
            out.append(f"{key}={val}")  # left blank on purpose (API keys, etc.)
    target.write_text("\n".join(out) + "\n", encoding="utf-8")
    return generated, kept


def main() -> None:
    env_ex = ROOT / ".env.example"
    env = ROOT / ".env"
    fe_ex = ROOT / "frontend" / ".env.local.example"
    fe = ROOT / "frontend" / ".env.local"

    g1, k1 = _fill(env_ex, env)
    # frontend AUTH_SECRET isn't in GEN's list — mint it here if still blank
    GEN["AUTH_SECRET"] = lambda: secrets.token_urlsafe(32)
    values = dict(
        line.split("=", 1)
        for line in env.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#") and "=" in line
    )
    g2, k2 = _fill(fe_ex, fe, {"KEYCLOAK_CLIENT_SECRET": values["KEYCLOAK_SECRET_WEB"]})

    print(f".env               : {g1} generated, {k1} kept")
    print(f"frontend/.env.local: {g2} generated, {k2} kept")
    missing = [
        k
        for k in API_KEYS
        if not any(
            line.startswith(f"{k}=") and line.strip() != f"{k}="
            for line in env.read_text(encoding="utf-8").splitlines()
        )
    ]
    if missing:
        print("\nStill blank - set these by hand in .env before `make up`:")
        for k in missing:
            print(f"  {k}")


if __name__ == "__main__":
    main()
