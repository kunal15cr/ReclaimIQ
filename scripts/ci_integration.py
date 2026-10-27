"""Run real integration checks without hosted model calls.

Default: own an isolated, disposable Compose project and a frontend process.
--existing-stack: check an already-running local app without stopping its services
or deleting any volumes. The AI worker must be stopped in either mode.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen

from gen_secrets import GEN
from manage import ROOT, compose, run


def mask_bootstrap_secrets() -> None:
    if os.getenv("GITHUB_ACTIONS") != "true":
        return
    for path in (ROOT / ".env", ROOT / "frontend/.env.local"):
        for line in path.read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            if value and (
                key in GEN
                or key
                in {
                    "AUTH_SECRET",
                    "KEYCLOAK_CLIENT_SECRET",
                    "VAULT_DEV_ROOT_TOKEN_ID",
                    "OPENAI_API_KEY",
                    "GROQ_API_KEY",
                }
            ):
                print(f"::add-mask::{value}", flush=True)


def wait_http(url: str, process: subprocess.Popen | None = None) -> None:
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            raise RuntimeError(
                "frontend exited before becoming ready; see .local-tools/ci/frontend.log"
            )
        try:
            with urlopen(url, timeout=5) as response:
                if response.status == 200:
                    return
        except (OSError, URLError):
            pass
        time.sleep(2)
    raise RuntimeError(f"timed out waiting for {url}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--existing-stack", action="store_true")
    existing = parser.parse_args().existing_stack
    frontend = None
    frontend_log = None
    owns_stack = False
    try:
        if not existing:
            # Cleanup can only affect this newly owned namespace, never the user's dev stack.
            os.environ["COMPOSE_PROJECT_NAME"] = "reclaimiq-ci-" + str(os.getpid())
            run(sys.executable, str(ROOT / "scripts/gen_secrets.py"))
            mask_bootstrap_secrets()
            owns_stack = True
            run(sys.executable, str(ROOT / "scripts/manage.py"), "local")
            run(
                "npm.cmd" if os.name == "nt" else "npm",
                "run",
                "build",
                cwd=ROOT / "frontend",
            )
            output = ROOT / ".local-tools/ci"
            output.mkdir(parents=True, exist_ok=True)
            frontend_log = (output / "frontend.log").open("w", encoding="utf-8")
            frontend = subprocess.Popen(
                [
                    "node",
                    "node_modules/next/dist/bin/next",
                    "start",
                    "--port",
                    "3000",
                    "--hostname",
                    "127.0.0.1",
                ],
                cwd=ROOT / "frontend",
                stdout=frontend_log,
                stderr=subprocess.STDOUT,
                start_new_session=os.name != "nt",
            )
        running = compose(
            "ps", "--status", "running", "--services", capture=True
        ).splitlines()
        if "worker" in running:
            raise RuntimeError(
                "stop the AI worker before running checks; no model calls are part of CI"
            )
        wait_http("http://127.0.0.1:8000/health")
        wait_http("http://127.0.0.1:3000", frontend)
        run(sys.executable, str(ROOT / "scripts/manage.py"), "opa-test")
        for script in ("verify_m2.py", "verify_local.py"):
            run(sys.executable, "-u", str(ROOT / "scripts" / script))
        run(sys.executable, str(ROOT / "scripts/manage.py"), "verify-worker-local")
        for script in (
            "verify_m2_frontend.py",
            "verify_frontend_session.py",
            "verify_audit_chain.py",
        ):
            run(sys.executable, "-u", str(ROOT / "scripts" / script))
        print("CI integration: all checks passed (no hosted model calls).", flush=True)
    finally:
        if frontend is not None and frontend.poll() is None:
            if os.name == "nt":
                frontend.terminate()
            else:
                os.killpg(frontend.pid, signal.SIGTERM)
            try:
                frontend.wait(timeout=10)
            except subprocess.TimeoutExpired:
                if os.name == "nt":
                    frontend.kill()
                else:
                    os.killpg(frontend.pid, signal.SIGKILL)
                frontend.wait()
        if frontend_log is not None:
            frontend_log.close()
        if owns_stack:
            # Always attempt cleanup; preserve an earlier verification failure's exit status.
            cleanup = subprocess.run(
                [
                    "docker",
                    "compose",
                    "--profile",
                    "observability",
                    "--profile",
                    "load",
                    "down",
                    "--volumes",
                    "--remove-orphans",
                ],
                cwd=ROOT,
                check=False,
            )
            if cleanup.returncode and sys.exc_info()[0] is None:
                raise RuntimeError(
                    "checks passed, but the isolated CI stack could not be cleaned up"
                )


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
