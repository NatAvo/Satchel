"""Finds addon downloads on WoWInterface.

WoWInterface doesn't tag addons for Forever, so support is decided by the TOC
check that every package goes through before it is installed.
"""

from __future__ import annotations

import html
import json
import re
import urllib.error
from urllib.parse import urlparse

from satchel.download import Release, open_url
from satchel.store import SatchelError

API = "https://api.mmoui.com/v4/game/WOW"
SOURCE = "wowinterface"


def _is_wowinterface_host(hostname: str | None) -> bool:
    return hostname == "wowinterface.com" or (hostname or "").endswith(".wowinterface.com")


def parse_id(text: str) -> str:
    """Accepts a numeric addon ID or a wowinterface.com addon URL."""
    text = text.strip()
    if text.isascii() and text.isdigit():
        return text
    parsed = urlparse(text if "://" in text else f"https://{text}")
    if _is_wowinterface_host(parsed.hostname):
        match = re.search(r"/info(\d+)", parsed.path) or re.search(r"(?:^|&)id=(\d+)", parsed.query)
        if match:
            return match.group(1)
    raise SatchelError("Enter a WoWInterface addon ID or paste the addon page's URL.")


class WoWInterface:
    def latest(self, addon_id: str) -> Release:
        try:
            with open_url(f"{API}/filedetails/{addon_id}.json") as response:
                data = json.loads(response.read(5 * 1024 * 1024))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise SatchelError(f"WoWInterface has no addon with ID {addon_id}.") from exc
            raise SatchelError(f"WoWInterface returned an error: HTTP {exc.code}.") from exc
        except (OSError, TimeoutError, ValueError) as exc:
            raise SatchelError(f"Could not reach WoWInterface: {exc}") from exc

        detail = data[0] if isinstance(data, list) and data else None
        if not isinstance(detail, dict) or not detail.get("downloadUri"):
            raise SatchelError(f"WoWInterface has no download for addon ID {addon_id}.")

        url = detail["downloadUri"]
        parsed = urlparse(url)
        if parsed.scheme != "https" or not _is_wowinterface_host(parsed.hostname):
            raise SatchelError(f"WoWInterface gave an unexpected download address: {url}")

        checksum = str(detail.get("checksum") or "")
        return Release(
            source=SOURCE,
            repo=addon_id,
            tag=str(detail.get("version") or detail.get("lastUpdate") or "unknown"),
            asset_name=detail.get("fileName") or f"{addon_id}.zip",
            url=url,
            digest=f"md5:{checksum}" if re.fullmatch(r"[0-9a-fA-F]{32}", checksum) else "",
            title=html.unescape(detail.get("title") or ""),
        )
