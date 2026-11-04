"""Minimal STAC / Planetary Computer HTTP helpers (stdlib only). The transport is injectable so adapters can be
tested without network; tests using a fake transport are NOT evidence that the live service works."""
from __future__ import annotations
import json
import time
import urllib.request
from datetime import datetime, timezone


class RemoteError(RuntimeError):
    """A remote service call failed (network, HTTP error, malformed response)."""


def http_json(url: str, body: dict | None = None, headers: dict | None = None, timeout: float = 30.0) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception as exc:
        raise RemoteError(f"{url}: {type(exc).__name__}: {exc}") from exc


class PlanetaryComputer:
    """STAC search + anonymous SAS signing for Planetary Computer collections (Sentinel-1 RTC, JRC GSW)."""

    def __init__(self, stac_url: str, sas_url: str, subscription_key: str = "", timeout: float = 30.0, transport=http_json):
        self.stac_url, self.sas_url, self.timeout, self._http = stac_url.rstrip("/"), sas_url.rstrip("/"), timeout, transport
        self._hdr = {"Ocp-Apim-Subscription-Key": subscription_key} if subscription_key else {}
        self._tokens: dict[str, tuple[str, float]] = {}

    def search(self, collection: str, bbox, start: datetime | None = None, end: datetime | None = None,
               query: dict | None = None, limit: int = 100, max_pages: int | None = None) -> list[dict]:
        body = {"collections": [collection], "bbox": [round(float(v), 5) for v in bbox], "limit": limit}
        if start and end:
            body["datetime"] = f"{start:%Y-%m-%dT%H:%M:%SZ}/{end:%Y-%m-%dT%H:%M:%SZ}"
        if query:
            body["query"] = query
        items, url, page, method, seen = [], f"{self.stac_url}/search", 0, "POST", set()
        while url and (max_pages is None or page < max_pages):
            page_key = (url, method, json.dumps(body, sort_keys=True) if method == "POST" else "")
            if page_key in seen:
                raise RemoteError("STAC pagination repeated a page request; search stopped to avoid silently omitting items")
            seen.add(page_key)
            resp = self._http(url, body, self._hdr, self.timeout)
            items += resp.get("features", [])
            nxt = next((l for l in resp.get("links", []) if l.get("rel") == "next"), None)
            if nxt:
                url = nxt["href"]
                method = nxt.get("method", "GET").upper()
                if method == "POST":
                    body = {**(body or {}), **(nxt.get("body") or {})}
                else:
                    body = None
            else:
                url = None
            page += 1
        if url:
            raise RemoteError(f"STAC search stopped at the configured {max_pages}-page limit before all results were retrieved")
        items.sort(key=lambda item: (item.get("properties") or {}).get("datetime") or "", reverse=True)
        return items

    def token(self, collection: str) -> str:
        tok = self._tokens.get(collection)
        if tok and tok[1] - 60 > time.time():
            return tok[0]
        resp = self._http(f"{self.sas_url}/{collection}", None, self._hdr, self.timeout)
        try:
            token = resp["token"]
        except KeyError as exc:
            raise RemoteError(f"SAS token response for {collection} has no 'token'") from exc
        exp = resp.get("msft:expiry")
        try:
            exp_ts = datetime.fromisoformat(str(exp).replace("Z", "+00:00")).timestamp() if exp else time.time() + 600
        except ValueError:
            exp_ts = time.time() + 600
        self._tokens[collection] = (token, exp_ts)
        return token

    def sign(self, href: str, collection: str) -> str:
        return href + ("&" if "?" in href else "?") + self.token(collection)


def parse_dt(value: str) -> datetime:
    d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
