# /// script
# requires-python = ">=3.12"
# dependencies = ["aiohttp>=3.12,<4", "PyJWT[crypto]>=2.10,<3", "playwright>=1.50,<2"]
# ///
"""Create a dedicated E.ON session in a temporary, user-operated browser."""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import runpy
import secrets
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import aiohttp
import jwt

CONSTANTS = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "custom_components/eon_next/const.py")
)
CLIENT_ID = CONSTANTS["AUTH0_CLIENT_ID"]
ISSUER = CONSTANTS["AUTH0_ISSUER"]
CALLBACK = (
    "com.eonnextmobile.app.auth0://auth.eonnext.com/"
    "android/com.eonnextmobile.app/callback"
)


def parse_callback(location: str, state: str) -> str | None:
    """Accept only this invocation's exact callback and state."""
    parsed, expected = urlsplit(location), urlsplit(CALLBACK)
    if (parsed.scheme, parsed.netloc, parsed.path) != (
        expected.scheme,
        expected.netloc,
        expected.path,
    ):
        return None
    values = parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.fragment
        or values.get("state") != [state]
        or "error" in values
        or len(values.get("code", [])) != 1
        or not values["code"][0]
    ):
        raise ValueError("Invalid login callback")
    return values["code"][0]


def save_session(output: Path, email: str, refresh_token: str) -> None:
    """Never overwrite an existing session or follow a pre-existing symlink."""
    fd = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump({"email": email, "refresh_token": refresh_token}, stream)
        stream.flush()
        os.fsync(stream.fileno())


async def sign_in(output: Path) -> None:
    from playwright.async_api import async_playwright

    if await asyncio.to_thread(output.exists):
        raise FileExistsError("Output already exists")
    verifier = secrets.token_urlsafe(48)
    state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
    url = (
        ISSUER
        + "authorize?"
        + urlencode(
            {
                "client_id": CLIENT_ID,
                "redirect_uri": CALLBACK,
                "response_type": "code",
                "scope": "openid offline_access email",
                "audience": "eonnext",
                "prompt": "login",
                "state": state,
                "nonce": nonce,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
    )
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=False)
        try:
            page = await browser.new_page()
            cdp = await page.context.new_cdp_session(page)
            result = asyncio.get_running_loop().create_future()

            async def on_response(event):
                try:
                    location = next(
                        (
                            h["value"]
                            for h in event.get("responseHeaders", [])
                            if h["name"].lower() == "location"
                        ),
                        "",
                    )
                    code = parse_callback(location, state)
                    if code:
                        # Finish this browser-owned flow without launching the
                        # installed mobile app or exposing the authorization code.
                        await cdp.send(
                            "Fetch.fulfillRequest",
                            {
                                "requestId": event["requestId"],
                                "responseCode": 200,
                                "responseHeaders": [
                                    {"name": "Content-Type", "value": "text/plain"},
                                    {"name": "Cache-Control", "value": "no-store"},
                                ],
                                "body": base64.b64encode(
                                    b"Home Assistant: sign-in received."
                                ).decode(),
                            },
                        )
                        if not result.done():
                            result.set_result(code)
                    else:
                        await cdp.send(
                            "Fetch.continueRequest", {"requestId": event["requestId"]}
                        )
                except Exception:
                    if not result.done():
                        result.set_exception(ValueError("Login callback failed"))

            cdp.on("Fetch.requestPaused", on_response)
            await cdp.send(
                "Fetch.enable",
                {
                    "patterns": [
                        {
                            "urlPattern": ISSUER + "*",
                            "requestStage": "Response",
                            "resourceType": "Document",
                        }
                    ]
                },
            )
            print("Sign in on E.ON's page in the new browser window.")
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            code = await asyncio.wait_for(result, timeout=900)
            async with aiohttp.ClientSession() as http:
                async with http.post(
                    ISSUER + "oauth/token",
                    json={
                        "grant_type": "authorization_code",
                        "client_id": CLIENT_ID,
                        "redirect_uri": CALLBACK,
                        "code": code,
                        "code_verifier": verifier,
                    },
                    allow_redirects=False,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response:
                    if response.status != 200:
                        raise ValueError("Token exchange failed")
                    tokens = await response.json()
                header = jwt.get_unverified_header(tokens["id_token"])
                if header.get("alg") != "RS256":
                    raise ValueError("Unexpected signing algorithm")
                async with http.get(
                    ISSUER + ".well-known/jwks.json",
                    allow_redirects=False,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response:
                    response.raise_for_status()
                    keys = (await response.json())["keys"]
                key = next(k for k in keys if k.get("kid") == header.get("kid"))
                claims = jwt.decode(
                    tokens["id_token"],
                    jwt.PyJWK.from_dict(key).key,
                    algorithms=["RS256"],
                    audience=CLIENT_ID,
                    issuer=ISSUER,
                    options={
                        "require": [
                            "exp",
                            "iat",
                            "iss",
                            "aud",
                            "sub",
                            "email",
                            "nonce",
                        ],
                        "strict_aud": True,
                    },
                )
                if not secrets.compare_digest(claims["nonce"], nonce):
                    raise ValueError("Nonce mismatch")
                if (
                    not isinstance(tokens.get("refresh_token"), str)
                    or not tokens["refresh_token"]
                ):
                    raise ValueError("No refresh token returned")
                await asyncio.to_thread(
                    save_session, output, claims["email"], tokens["refresh_token"]
                )
            print(f"Session saved privately to {output}. Treat it like a password.")
            print("Enter its email and refresh_token in the E.ON Next integration.")
            print("Delete this file after Home Assistant has accepted the session.")
        finally:
            await browser.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("eon-next-session.json"))
    args = parser.parse_args()
    try:
        asyncio.run(sign_in(args.output))
    except KeyboardInterrupt:
        print("Login cancelled.")
        return 1
    except Exception as error:
        # Never print browser errors containing URLs, codes, or response bodies.
        print(
            f"Login did not complete ({type(error).__name__}). No HA settings changed."
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
