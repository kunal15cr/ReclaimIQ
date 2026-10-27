"""Verify a real Keycloak browser-style login and the server-side token proxy."""

from __future__ import annotations

import secrets
import uuid
from html.parser import HTMLParser

from _kc import KC, REALM, admin_token, register_user
from _rg import Check, httpx

FRONT = "http://localhost:3000"


class LoginForm(HTMLParser):
    action: str | None = None

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "form" and values.get("id") == "kc-form-login":
            self.action = values.get("action")


def main() -> None:
    c = Check("verify_frontend_session")
    email = "frontend-" + uuid.uuid4().hex[:10] + "@test.local"
    password = secrets.token_urlsafe(24)
    uid = register_user(email, password)
    try:
        with httpx.Client(follow_redirects=True, timeout=30) as client:
            csrf = client.get(FRONT + "/api/auth/csrf").json()["csrfToken"]
            response = client.post(
                FRONT + "/api/auth/signin/keycloak",
                data={
                    "csrfToken": csrf,
                    "callbackUrl": FRONT + "/orders",
                },
            )
            response.raise_for_status()
            form = LoginForm()
            form.feed(response.text)
            if not form.action:
                raise RuntimeError("Keycloak login form did not load")
            # Browsers treat localhost as trustworthy for Secure cookies; httpx
            # requires HTTPS. Match the browser behavior for this loopback test.
            for cookie in client.cookies.jar:
                if cookie.domain in ("localhost", "localhost.local"):
                    cookie.secure = False
            response = client.post(
                form.action, data={"username": email, "password": password}
            )
            if response.status_code >= 400:
                import re

                detail = re.sub(r"<[^>]+>", " ", response.text)
                raise RuntimeError(
                    f"Keycloak login failed ({response.status_code}): {' '.join(detail.split())[-500:]}"
                )
            c.ok(
                response.url.path == "/orders",
                "OIDC login returns to the authenticated orders page",
            )
            public_session = client.get(FRONT + "/api/auth/session").json()
            c.ok(
                public_session.get("user", {}).get("email") == email,
                "the public session identifies the real account",
            )
            c.ok(
                "accessToken" not in public_session
                and "refreshToken" not in public_session,
                "the public session does not expose provider bearer tokens",
            )
            result = client.get(FRONT + "/api/rg/orders")
            c.ok(
                result.status_code == 200 and isinstance(result.json(), list),
                "the server proxy decrypts the HttpOnly cookie and authenticates to FastAPI",
            )
            c.ok(
                client.post(
                    FRONT + "/api/rg/dashboard/returns/" + str(uuid.uuid4()) + "/refund"
                ).status_code
                == 403,
                "the customer session cannot invoke staff refund authorization",
            )
        c.done()
    finally:
        httpx.put(
            f"{KC}/admin/realms/{REALM}/users/{uid}",
            headers={"Authorization": "Bearer " + admin_token()},
            json={"enabled": False},
            timeout=15,
        ).raise_for_status()


if __name__ == "__main__":
    main()
