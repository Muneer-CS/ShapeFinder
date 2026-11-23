"""Authenticate the public gateway's checks and all direct hosted API requests."""

import binascii
import hashlib
import secrets
from base64 import b64decode

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

HEALTH_PATH = "/__health"
AUTH_PATH = "/__owner_auth"


class OwnerAccessMiddleware:
    def __init__(self, app: ASGIApp, *, username: str, password: str, origin: str) -> None:
        self.app = app
        self.username = hashlib.sha256(username.encode("ascii")).digest()
        self.password = hashlib.sha256(password.encode("ascii")).digest()
        self.origin = origin

    def _signed_in(self, headers: Headers) -> bool:
        supplied = headers.getlist("authorization")
        if len(supplied) != 1:
            return False
        scheme, _, encoded = supplied[0].partition(" ")
        if scheme.lower() != "basic" or not encoded or len(encoded) > 512:
            return False
        try:
            user, colon, password = b64decode(encoded, validate=True).partition(b":")
        except (binascii.Error, ValueError):
            return False
        user_ok = secrets.compare_digest(hashlib.sha256(user).digest(), self.username)
        pass_ok = secrets.compare_digest(hashlib.sha256(password).digest(), self.password)
        return bool(colon) and user_ok and pass_ok

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        if scope["type"] != "http" or (scope["path"] == HEALTH_PATH and scope["method"] == "GET"):
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        if not self._signed_in(headers):
            await JSONResponse(
                {"error": "Owner sign-in required."},
                status_code=401,
                headers={
                    "WWW-Authenticate": 'Basic realm="ShapeFinder"',
                    "Cache-Control": "private, no-store",
                },
            )(scope, receive, send)
            return
        origin = headers.get("origin")
        if scope["method"] not in {"GET", "HEAD", "OPTIONS"} and (
            (origin is not None and origin != self.origin)
            or headers.get("sec-fetch-site") == "cross-site"
        ):
            await JSONResponse(
                {"error": "Send this request from the ShapeFinder origin."},
                status_code=403,
                headers={"Cache-Control": "private, no-store"},
            )(scope, receive, send)
            return

        async def private_response(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["Cache-Control"] = "private, no-store"
            await send(message)

        await self.app(scope, receive, private_response)
