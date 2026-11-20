"""Publishes Satchel.exe as a GitHub release, which Satchel then offers as an update.

    python tools/release.py "What changed, in a sentence or two."

Bump __version__ in satchel/__init__.py and commit first. The script refuses to run with
uncommitted changes or unpushed commits, runs the tests, builds dist/Satchel.exe, tags
v<version>, and creates the release with the exe attached.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from satchel import __version__  # noqa: E402
from satchel.selfupdate import ASSET, REPO  # noqa: E402


def run(*command: str, capture: bool = False) -> str:
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=capture)
    if result.returncode != 0:
        sys.exit(f"Failed: {' '.join(command)}\n{result.stderr or ''}")
    return result.stdout.strip() if capture else ""


def main() -> None:
    if len(sys.argv) != 2 or not sys.argv[1].strip():
        sys.exit(__doc__)
    notes = sys.argv[1].strip()
    tag = f"v{__version__}"

    if run("git", "status", "--porcelain", capture=True):
        sys.exit("Commit or stash your changes first.")
    run("git", "fetch", "origin", capture=True)
    branch = run("git", "rev-parse", "--abbrev-ref", "HEAD", capture=True)
    if run("git", "rev-list", f"origin/{branch}..HEAD", capture=True):
        sys.exit(f"Push {branch} first.")
    if run("git", "tag", "--list", tag, capture=True):
        sys.exit(f"{tag} already exists. Bump __version__ in satchel/__init__.py.")

    run(sys.executable, "-m", "pytest", "-q")
    run(sys.executable, "-m", "PyInstaller", "--noconfirm", "--noconsole", "--onefile", "--name", "Satchel",
        "--icon", r"satchel\assets\satchel.ico", "--add-data", r"satchel\assets;satchel\assets",
        r"satchel\__main__.py")
    exe = ROOT / "dist" / ASSET

    run("git", "tag", "-a", tag, "-m", f"Satchel {__version__}")
    run("git", "push", "origin", tag)
    run("gh", "release", "create", tag, str(exe), "--repo", REPO, "--title", f"Satchel {__version__}",
        "--notes", notes)
    print(f"Published {tag}. Satchel.exe {__version__} and older will offer it at launch.")


if __name__ == "__main__":
    main()
