"""JWKS (JSON Web Key Set) client — Phase 8b.4.

The ID-token verifier needs the IdP's public signing keys to validate
ID-token signatures. IdPs publish those keys at a ``jwks_uri`` endpoint
(advertised in the OIDC discovery document) and rotate them on their
own schedule. This client:

* caches the key map ``{kid -> jwk_dict}`` in memory
* refreshes when the cache is empty, the requested kid is missing, or
  the TTL has elapsed
* coalesces concurrent refresh attempts behind an ``asyncio.Lock`` so
  a thundering herd of incoming callbacks triggers a single HTTP fetch
* respects ``Cache-Control: max-age`` from the JWKS response when
  present, falls back to the configured TTL otherwise

What it deliberately does NOT do:

* No on-disk cache — process-local is enough; a new worker warms in
  one request.
* No background refresh — we refresh lazily on demand. The first
  request after expiry pays the ~50ms HTTP cost; everything else is
  served from memory.
* No retry/backoff inside ``get_key`` — the OAuth callback handler
  surfaces fetch failures to the user as a 502, then the next attempt
  retries naturally. Burying retries here would hide real outages.
"""

from __future__ import annotations

import asyncio
import re
import time

import httpx

# Default cache TTL when the JWKS response has no Cache-Control header.
# Google rotates daily; an hour is a reasonable conservative default.
_DEFAULT_TTL_SECONDS: int = 3600

# Cap responses to 1 MiB — JWKS documents are tiny (< 5 KB for most
# IdPs). Anything larger is either misconfigured or hostile.
_MAX_JWKS_BYTES: int = 1 * 1024 * 1024

# Total per-fetch wall-clock budget. Generous because some IdP JWKS
# endpoints sit behind a CDN with a cold-cache first-byte latency in
# the seconds.
_FETCH_TIMEOUT_SECONDS: float = 10.0

_MAX_AGE_RE: re.Pattern[str] = re.compile(r"max-age\s*=\s*(\d+)", re.IGNORECASE)


class JWKSError(Exception):
    """Base class — catch this to handle any JWKS-related failure."""


class JWKSFetchError(JWKSError):
    """Network failure, non-2xx response, or upstream timeout."""


class JWKSInvalidResponseError(JWKSError):
    """Response was reachable but not a valid JWKS document."""


class JWKSKeyNotFoundError(JWKSError):
    """A specific ``kid`` was not present in the JWKS even after refresh."""


class JWKSClient:
    """In-memory JWKS cache with on-demand refresh + request coalescing.

    Typical lifecycle: one instance per OAuth provider, constructed at
    extension startup, kept alive for the process. Multiple callers
    can share the same instance — refresh is concurrency-safe.
    """

    __slots__ = (
        "_default_ttl",
        "_http_client",
        "_jwks_uri",
        "_keys",
        "_lock",
        "_owns_client",
        "_valid_until",
    )

    def __init__(
        self,
        jwks_uri: str,
        *,
        ttl_seconds: int = _DEFAULT_TTL_SECONDS,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._jwks_uri = jwks_uri
        self._default_ttl = ttl_seconds
        # Caller may inject a shared httpx client (typical in tests).
        # When they do, we don't own it and must not close it on
        # ``aclose``. When we create our own, we own it.
        self._http_client = http_client or httpx.AsyncClient(
            timeout=_FETCH_TIMEOUT_SECONDS
        )
        self._owns_client = http_client is None
        self._keys: dict[str, dict] = {}
        self._valid_until: float = 0.0
        self._lock = asyncio.Lock()

    async def aclose(self) -> None:
        """Release the underlying HTTP client if we own it."""
        if self._owns_client:
            await self._http_client.aclose()

    async def get_key(self, kid: str) -> dict:
        """Return the JWK with the matching ``kid``.

        Refreshes the cache on miss / expiry. Concurrent callers share
        a single in-flight refresh — the Nth caller during a cache miss
        triggers zero extra HTTP fetches.
        """
        # Fast path: still inside TTL and key is known. No lock needed
        # — the dict read is atomic in CPython and stale-but-valid is
        # acceptable here (we may serve a key the IdP just removed; the
        # token verifier will fail signature check and the caller will
        # retry, which forces a refresh).
        if time.monotonic() < self._valid_until and kid in self._keys:
            return self._keys[kid]

        async with self._lock:
            # Re-check inside the lock — another coroutine may have
            # refreshed while we were waiting. This is the coalescing
            # half of the read-then-refresh dance: only one fetch runs,
            # everyone else picks up the result.
            if time.monotonic() < self._valid_until and kid in self._keys:
                return self._keys[kid]
            await self._refresh_locked()

        if kid not in self._keys:
            # Refreshed and STILL missing — the IdP has rotated the
            # key out entirely. Treat as a hard failure so the verifier
            # surfaces a meaningful 401, rather than e.g. silently
            # falling back to a stale cached key.
            raise JWKSKeyNotFoundError(
                f"kid={kid!r} not present in JWKS from {self._jwks_uri}"
            )
        return self._keys[kid]

    async def _refresh_locked(self) -> None:
        """Fetch + replace the entire key map. Caller holds ``self._lock``."""
        try:
            resp = await self._http_client.get(self._jwks_uri)
        except httpx.HTTPError as exc:
            raise JWKSFetchError(
                f"JWKS fetch failed: {exc.__class__.__name__}: {exc}"
            ) from exc

        if resp.status_code >= 400:
            raise JWKSFetchError(
                f"JWKS endpoint returned HTTP {resp.status_code} "
                f"from {self._jwks_uri}"
            )

        # Refuse silently-truncated 1MB+ responses — we cap reads via
        # ``content`` bounded by Content-Length, but a malicious server
        # could stream a body without a length header. httpx's default
        # is fine for sane endpoints; this is belt-and-braces.
        body = resp.content
        if len(body) > _MAX_JWKS_BYTES:
            raise JWKSInvalidResponseError(
                f"JWKS response too large: {len(body)} > {_MAX_JWKS_BYTES} bytes"
            )

        try:
            document = resp.json()
        except ValueError as exc:
            raise JWKSInvalidResponseError("JWKS response is not JSON") from exc

        keys = document.get("keys") if isinstance(document, dict) else None
        if not isinstance(keys, list) or not keys:
            raise JWKSInvalidResponseError(
                "JWKS response missing or empty 'keys' array"
            )

        new_map: dict[str, dict] = {}
        for jwk in keys:
            if not isinstance(jwk, dict):
                continue
            kid = jwk.get("kid")
            if isinstance(kid, str) and kid:
                new_map[kid] = jwk
        if not new_map:
            raise JWKSInvalidResponseError(
                "JWKS contains no keys with a 'kid' field"
            )

        # TTL: prefer the response's Cache-Control max-age, fall back
        # to our configured default. Clamp to a reasonable floor so a
        # misconfigured IdP returning max-age=0 doesn't make us refetch
        # on every request.
        ttl = _ttl_from_response(resp) or self._default_ttl
        ttl = max(ttl, 60)

        self._keys = new_map
        self._valid_until = time.monotonic() + ttl


def _ttl_from_response(resp: httpx.Response) -> int | None:
    """Parse ``Cache-Control: max-age=N`` from a JWKS response.

    Returns the integer max-age in seconds, or None if the header is
    absent / unparseable. We don't try to honour ``s-maxage``,
    ``no-cache``, etc. — JWKS responses in the wild use a plain
    ``max-age=...`` and that's all the spec requires.
    """
    header = resp.headers.get("cache-control", "")
    match = _MAX_AGE_RE.search(header)
    if not match:
        return None
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return None
