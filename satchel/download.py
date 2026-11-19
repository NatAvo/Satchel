"""Downloads a release from any source and installs it."""

from __future__ import annotations

import hashlib
import shutil
import tempfile
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, urlparse

from satchel import __version__
from satchel.store import Entry, SatchelError, Store, inspect_zip

USER_AGENT = f"Satchel/{__version__}"
TIMEOUT = 60
MAX_DOWNLOAD_BYTES = 1024**3


@dataclass(frozen=True)
class Release:
    source: str  # "github" or "wowinterface"
    repo: str  # The addon's identifier at that source
    tag: str
    asset_name: str
    url: str
    digest: str = ""  # "sha256:<hex>", "md5:<hex>", or empty if the source gives none
    title: str = ""
    # GitHub token for a release that must be fetched through the API (private
    # repositories). Never shown, compared or saved; see open_url.
    token: str = field(default="", repr=False, compare=False)


def entry_id(source: str, repo: str) -> str:
    return f"{source}:{repo.lower()}"


def page_url(source: str, repo: str) -> str | None:
    """Returns the addon's web page, or None if its source doesn't have one."""
    if not repo:
        return None
    if source == "github":
        return f"https://github.com/{quote(repo, safe='/')}"
    if source == "wowinterface":
        return f"https://www.wowinterface.com/downloads/info{quote(repo, safe='')}"
    return None


def open_url(url: str, token: str = ""):
    """Opens a URL. Without a token, credentials are never sent.

    With a token, the URL must be a GitHub API release-asset URL. The token goes in an
    unredirected header, so the redirect GitHub answers with (to its file storage)
    does not carry it.
    """
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    if token:
        if urlparse(url).hostname != "api.github.com":
            raise SatchelError("Refusing to send a GitHub token anywhere but api.github.com.")
        request.add_header("Accept", "application/octet-stream")
        request.add_unredirected_header("Authorization", f"Bearer {token}")
    return urllib.request.urlopen(request, timeout=TIMEOUT)


def download_release(release: Release, destination: Path) -> None:
    algorithm, _, expected = release.digest.partition(":")
    digest = hashlib.new(algorithm or "sha256")
    size = 0
    try:
        opened = open_url(release.url, release.token) if release.token else open_url(release.url)
        with opened as response, open(destination, "wb") as output:
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_DOWNLOAD_BYTES:
                    raise SatchelError(f"{release.asset_name} is too large to download.")
                digest.update(chunk)
                output.write(chunk)
    except (OSError, TimeoutError) as exc:
        raise SatchelError(f"Could not download {release.asset_name}: {exc}") from exc
    if expected and expected.lower() != digest.hexdigest():
        raise SatchelError(f"{release.asset_name} failed its checksum, so it was not installed.")


def install_release(store: Store, release: Release, allow_unverified: bool = False) -> Entry:
    workspace = Path(tempfile.mkdtemp(prefix="download-", dir=store.work_dir))
    try:
        zip_path = workspace / "package.zip"
        download_release(release, zip_path)
        folders = inspect_zip(zip_path, store.install.interface)
        primary = min(folders, key=lambda folder: (len(folder), folder.lower()))
        return store.install_zip(
            zip_path,
            id=entry_id(release.source, release.repo),
            name=release.title or folders[primary].title,
            source=release.source,
            version=release.tag,
            repo=release.repo,
            asset=release.asset_name,
            allow_unverified=allow_unverified,
        )
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
