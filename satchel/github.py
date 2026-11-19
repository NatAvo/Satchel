"""Finds Forever builds in GitHub Releases."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from urllib.parse import urlparse

from satchel.download import TIMEOUT, USER_AGENT, Release, open_url
from satchel.store import SatchelError

API = "https://api.github.com"
SOURCE = "github"

_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_FOREVER_NAME = re.compile(r"(forever|camelot)", re.IGNORECASE)
_OTHER_FLAVOR_NAME = re.compile(
    r"(classic|vanilla|tbc|bcc|wrath|wotlk|cata|mists|mop|mainline|retail|nolib)", re.IGNORECASE
)


def parse_repo(text: str) -> str:
    """Accepts owner/repo or any github.com URL for the repository."""
    text = text.strip()
    if "://" in text or text.lower().startswith("github.com/"):
        parsed = urlparse(text if "://" in text else f"https://{text}")
        if parsed.hostname not in ("github.com", "www.github.com"):
            raise SatchelError("Only github.com repositories are supported.")
        text = "/".join(parsed.path.strip("/").split("/")[:2])
    text = text.removesuffix(".git")
    if not _REPO.match(text):
        raise SatchelError("Enter a GitHub repository as owner/name or paste its URL.")
    return text


def select_asset(assets: list[dict], release_json: dict | None) -> dict | None:
    """Picks the release ZIP intended for Forever, or None if none is identifiable."""
    zips = {asset["name"]: asset for asset in assets if asset["name"].lower().endswith(".zip")}

    if release_json:
        for release in release_json.get("releases", []):
            if release.get("nolib"):
                continue
            flavors = {item.get("flavor") for item in release.get("metadata", [])}
            if "forever" in flavors and release.get("filename") in zips:
                return zips[release["filename"]]

    named = [asset for name, asset in zips.items() if _FOREVER_NAME.search(name)]
    if named:
        return named[0]

    # A lone unflavored ZIP is a candidate. Its TOC files are checked after download.
    plain = [asset for name, asset in zips.items() if not _OTHER_FLAVOR_NAME.search(name)]
    if len(plain) == 1 and not (release_json and release_json.get("releases")):
        return plain[0]
    return None


def gh_token() -> str | None:
    """The token of the GitHub CLI login (`gh auth login`), if there is one."""
    gh = shutil.which("gh") or r"C:\Program Files\GitHub CLI\gh.exe"
    try:
        result = subprocess.run(
            [gh, "auth", "token", "--hostname", "github.com"],
            capture_output=True, text=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    token = result.stdout.strip()
    return token if result.returncode == 0 and token else None


class GitHub:
    def __init__(self, token: str | None = None):
        # None means "find one": GITHUB_TOKEN first, then the GitHub CLI login. The
        # lookup waits until the first request, so starting Satchel stays fast.
        self._token = token
        self._looked_up = token is not None

    @property
    def token(self) -> str | None:
        if not self._looked_up:
            self._token = os.environ.get("GITHUB_TOKEN") or gh_token()
            self._looked_up = True
        return self._token

    def _open(self, url: str):
        """Opens an API URL. This is the only request that can carry the token."""
        headers = {"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=TIMEOUT)

    def _api(self, path: str) -> dict:
        try:
            with self._open(f"{API}{path}") as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                hint = "" if self.token else (
                    " If it's a private repository, log in with `gh auth login` "
                    "or set GITHUB_TOKEN, then restart Satchel.")
                raise SatchelError("GitHub has no published release for that repository." + hint) from exc
            if exc.code in (403, 429):
                raise SatchelError(
                    "GitHub's hourly limit was reached. Try again later, or set GITHUB_TOKEN "
                    "before starting Satchel for a higher limit."
                ) from exc
            raise SatchelError(f"GitHub returned an error: HTTP {exc.code}.") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise SatchelError(f"Could not reach GitHub: {exc}") from exc

    def latest(self, repo: str) -> Release:
        data = self._api(f"/repos/{repo}/releases/latest")
        tag = data["tag_name"]
        assets = data.get("assets", [])
        # A private repository's files can only be fetched through the API, with the
        # token. Public repositories keep using the plain download link, credential-free.
        private = self._is_private(repo)
        release_json = None
        for asset in assets:
            if asset["name"] == "release.json":
                release_json = self._read_json(asset, private)
                break
        asset = select_asset(assets, release_json)
        if asset is None:
            names = ", ".join(item["name"] for item in assets) or "no files"
            raise SatchelError(f"{repo} {tag} has no Forever build. Release files: {names}")
        if private:
            return Release(SOURCE, repo, tag, asset["name"], asset["url"], asset.get("digest") or "",
                           token=self.token or "")
        return Release(SOURCE, repo, tag, asset["name"], asset["browser_download_url"], asset.get("digest") or "")

    def _is_private(self, repo: str) -> bool:
        if not self.token:
            return False  # without a token, a private repository is invisible anyway
        try:
            return bool(self._api(f"/repos/{repo}").get("private"))
        except SatchelError:
            return False

    def _read_json(self, asset: dict, private: bool) -> dict | None:
        try:
            if private:
                opened = open_url(asset["url"], self.token or "")
            else:
                opened = open_url(asset["browser_download_url"])
            with opened as response:
                return json.loads(response.read(5 * 1024 * 1024))
        except (OSError, ValueError, SatchelError):
            return None
