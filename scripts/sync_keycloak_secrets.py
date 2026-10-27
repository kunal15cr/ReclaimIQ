"""Sync generated confidential-client credentials into an existing local realm.

Fresh imports receive these values via environment placeholders. This also
updates an existing database, whose realm import is intentionally not repeated.
Credentials are never printed. Requires the bootstrap .env, not provider keys.
"""

from __future__ import annotations

import json
import time
from http.client import HTTPException
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from gen_secrets import CLIENT_SECRETS, ROOT


def main() -> None:
    env = dict(
        line.split("=", 1)
        for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#") and "=" in line
    )
    base = "http://127.0.0.1:8081"
    deadline = time.monotonic() + 120
    while True:
        try:
            payload = urlencode(
                {
                    "grant_type": "password",
                    "client_id": "admin-cli",
                    "username": env["KEYCLOAK_ADMIN"],
                    "password": env["KEYCLOAK_ADMIN_PASSWORD"],
                }
            ).encode()
            with urlopen(
                Request(
                    base + "/realms/master/protocol/openid-connect/token", data=payload
                ),
                timeout=10,
            ) as response:
                token = json.load(response)["access_token"]
            break
        except (OSError, HTTPException):
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    "Keycloak admin login unavailable; no client credentials changed"
                ) from None
            time.sleep(2)
    headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
    template = json.loads(
        (ROOT / "infra/keycloak/realm-export.json").read_text(encoding="utf-8")
    )
    realm_url = base + "/admin/realms/returnguard"
    branding = {
        key: template[key]
        for key in ("displayName", "displayNameHtml")
        if key in template
    }
    if branding:
        with urlopen(
            Request(
                realm_url,
                headers=headers,
                data=json.dumps(branding).encode(),
                method="PUT",
            ),
            timeout=15,
        ):
            pass
    client_names = {
        client["clientId"]: client["name"]
        for client in template["clients"]
        if "name" in client
    }
    clients_url = base + "/admin/realms/returnguard/clients"
    with urlopen(Request(clients_url, headers=headers), timeout=15) as response:
        clients = json.load(response)
    updated = 0
    for client in clients:
        key = CLIENT_SECRETS.get(client["clientId"])
        if key:
            if not env.get(key):
                raise RuntimeError(f"generate missing credential {key} before syncing")
            with urlopen(
                Request(
                    clients_url + "/" + client["id"],
                    headers=headers,
                    data=json.dumps(
                        {
                            "secret": env[key],
                            **(
                                {"name": client_names[client["clientId"]]}
                                if client["clientId"] in client_names
                                else {}
                            ),
                        }
                    ).encode(),
                    method="PUT",
                ),
                timeout=15,
            ):
                updated += 1
    if updated != len(CLIENT_SECRETS):
        raise RuntimeError("some configured Keycloak clients are missing")
    print(f"Synced {updated} generated Keycloak client credentials (values redacted).")


if __name__ == "__main__":
    main()
