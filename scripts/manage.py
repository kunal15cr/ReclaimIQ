"""Cross-platform project tasks. Run `python scripts/manage.py --help`."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCAL = [
    "postgres",
    "redis",
    "qdrant",
    "minio",
    "minio-init",
    "mailhog",
    "vault",
    "vault-init",
    "keycloak",
    "opa",
    "backend",
]


def run(*args: str, cwd: Path = ROOT, capture: bool = False) -> str:
    result = subprocess.run(
        args,
        cwd=cwd,
        check=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=capture,
    )
    return result.stdout if capture else ""


def compose(*args: str, capture: bool = False) -> str:
    return run("docker", "compose", *args, capture=capture)


def wait_healthy(required: list[str], timeout: int = 300) -> None:
    deadline = time.monotonic() + timeout
    while True:
        raw = compose("ps", "--all", "--format", "json", capture=True).strip()
        rows = (
            json.loads(raw)
            if raw.startswith("[")
            else [json.loads(line) for line in raw.splitlines() if line.strip()]
        )
        services = {row["Service"]: row for row in rows}
        bad = []
        for name in required:
            row = services.get(name, {})
            state = row.get("State", "missing")
            if (
                name.endswith("-init")
                and state == "exited"
                and row.get("ExitCode") == 0
            ):
                continue
            if state != "running" or row.get("Health", "") not in ("", "healthy"):
                bad.append(f"{name}:{state}/{row.get('Health', '')}")
        if not bad:
            print("all requested services healthy", flush=True)
            return
        if time.monotonic() >= deadline:
            raise RuntimeError("timed out waiting for " + ", ".join(bad))
        print("waiting on " + ", ".join(bad), flush=True)
        time.sleep(3)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "task",
        choices=[
            "local",
            "up",
            "up-lite",
            "down",
            "migrate",
            "seed",
            "m4-setup",
            "verify-local",
            "verify-worker-local",
            "smoke",
            "frontend",
            "opa-test",
        ],
    )
    task = parser.parse_args().task
    if task == "frontend":
        run(
            "npm.cmd" if os.name == "nt" else "npm", "run", "dev", cwd=ROOT / "frontend"
        )
    elif task in ("smoke", "verify-local"):
        run(
            sys.executable,
            str(
                ROOT
                / "scripts"
                / ("smoke.py" if task == "smoke" else "verify_local.py")
            ),
        )
    elif task in ("local", "up", "up-lite"):
        run(sys.executable, str(ROOT / "scripts" / "gen_secrets.py"))
        run("docker", "info", capture=True)
        if task != "local":
            env = dict(
                line.split("=", 1)
                for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines()
                if line and not line.startswith("#") and "=" in line
            )
            if not all(env.get(key) for key in ("OPENAI_API_KEY", "GROQ_API_KEY")):
                raise RuntimeError(
                    "set OPENAI_API_KEY and GROQ_API_KEY in .env; use task 'local' without AI keys"
                )
        if task == "local":
            # Prevent an existing AI worker from processing verification submissions.
            compose("stop", "worker")
            compose("up", "-d", "--build", *LOCAL)
            wait_healthy(LOCAL)
            run(sys.executable, str(ROOT / "scripts" / "sync_keycloak_secrets.py"))
            compose("exec", "-T", "backend", "alembic", "upgrade", "head")
            compose("exec", "-T", "backend", "python", "-m", "seed.seed")
        else:
            profile = ["--profile", "observability"] if task == "up" else []
            compose(*profile, "up", "-d", "--build")
            required = compose(
                *profile, "config", "--services", capture=True
            ).splitlines()
            wait_healthy(required)
            run(sys.executable, str(ROOT / "scripts" / "sync_keycloak_secrets.py"))
    elif task == "down":
        compose("--profile", "observability", "--profile", "load", "down")
    elif task == "migrate":
        compose("run", "--rm", "backend", "alembic", "upgrade", "head")
    elif task == "seed":
        compose("run", "--rm", "backend", "python", "-m", "seed.seed")
    elif task == "verify-worker-local":
        compose(
            "-f",
            "docker-compose.yml",
            "-f",
            "docker-compose.verify.yml",
            "run",
            "--build",
            "--rm",
            "worker-check",
        )
    elif task == "opa-test":
        compose("exec", "-T", "opa", "/opa", "test", "/policies", "-v")
    elif task == "m4-setup":
        for module in ("ml.generate_dataset", "ml.train", "pipeline.policy_index"):
            compose("run", "--rm", "worker", "python", "-m", module)
        for script in ("mcp_setup.py", "bifrost_setup.py"):
            run(sys.executable, str(ROOT / "scripts" / script))
        compose("run", "--rm", "worker", "python", "-m", "pipeline.sync_prompts")
        compose("restart", "worker")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
