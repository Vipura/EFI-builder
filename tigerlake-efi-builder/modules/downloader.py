"""
downloader.py – GitHub Release Downloader for Tiger Lake EFI Builder
====================================================================

Downloads the latest RELEASE artefacts from GitHub for every component
required by an OpenCore Tiger Lake (11th-gen) EFI folder, then extracts
the relevant files into ``dist/EFI/``.

Environment variables
---------------------
GITHUB_TOKEN   (optional) – a GitHub Personal Access Token (classic or
               fine-grained with *public_repo* read) used for higher API
               rate limits (5 000 req/h vs. 60 req/h unauthenticated).

Usage
-----
>>> from modules.downloader import GitHubDownloader
>>> dl = GitHubDownloader(base_dir=Path("."))
>>> dl.download_all(hardware_report={})
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import time
import zipfile
from pathlib import Path
from typing import Any

import requests
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.table import Table

# ──────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────
GITHUB_API = "https://api.github.com"
RAW_GITHUB = "https://raw.githubusercontent.com"

# Retry / rate-limit settings
MAX_RETRIES: int = 3
RETRY_BACKOFF: float = 5.0          # seconds between retries
RATE_LIMIT_WAIT: float = 60.0       # wait when 403/rate-limited

# Subdirectories inside ``dist/``
EFI_SUBDIRS: list[str] = [
    "EFI/BOOT",
    "EFI/OC/ACPI",
    "EFI/OC/Drivers",
    "EFI/OC/Kexts",
    "EFI/OC/Resources",
    "EFI/OC/Tools",
]

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

console = Console(legacy_windows=False)


# ──────────────────────────────────────────────────────────────────────
# Helper: find a file inside a zip by name (case-insensitive)
# ──────────────────────────────────────────────────────────────────────
def _find_in_zip(zf: zipfile.ZipFile, name: str) -> str | None:
    """Return the first zip member whose basename matches *name* (case-insensitive)."""
    for member in zf.namelist():
        if member.endswith("/"):
            continue
        if Path(member).name.lower() == name.lower():
            return member
    return None


def _find_path_in_zip(zf: zipfile.ZipFile, suffix: str) -> str | None:
    """Return the first zip member whose path ends with *suffix* (case-insensitive)."""
    suffix_lower = suffix.replace("\\", "/").lower()
    for member in zf.namelist():
        if member.endswith("/"):
            continue
        if member.replace("\\", "/").lower().endswith(suffix_lower):
            return member
    return None


def _extract_directory_from_zip(
    zf: zipfile.ZipFile,
    dir_name: str,
    dest: Path,
) -> bool:
    """Extract a directory tree (e.g. ``Some.kext/``) from *zf* into *dest*.

    Returns ``True`` if at least one file was extracted.
    """
    # Normalise: we look for members that contain ``/dir_name/`` or start
    # with ``dir_name/`` so we can strip the prefix above it.
    members: list[str] = []
    prefix: str | None = None
    dir_name_slash = f"{dir_name}/"

    for m in zf.namelist():
        norm = m.replace("\\", "/")
        idx = norm.find(dir_name_slash)
        if idx != -1:
            members.append(m)
            if prefix is None:
                prefix = norm[:idx]

    if not members or prefix is None:
        return False

    for m in members:
        norm = m.replace("\\", "/")
        rel = norm[len(prefix):]          # e.g. "Some.kext/Contents/Info.plist"
        target = dest / rel
        if m.endswith("/"):
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(m) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
    return True


# ──────────────────────────────────────────────────────────────────────
# Main class
# ──────────────────────────────────────────────────────────────────────
class GitHubDownloader:
    """Download and extract OpenCore + kext components from GitHub."""

    def __init__(
        self,
        base_dir: Path | str = ".",
        cache_dir: Path | str | None = None,
        dist_dir: Path | str | None = None,
    ) -> None:
        self.base_dir = Path(base_dir).resolve()
        self.cache_dir = Path(cache_dir).resolve() if cache_dir else self.base_dir / "cache"
        self.dist_dir = Path(dist_dir).resolve() if dist_dir else self.base_dir / "dist"

        # HTTP session
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/vnd.github+json",
            "User-Agent": "TigerLake-EFI-Builder/1.0",
        })
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"
            console.print("[green]✓[/] Using GitHub token for higher rate limits")
        else:
            console.print(
                "[yellow]⚠[/] No GITHUB_TOKEN set – using unauthenticated "
                "requests (60 req/h limit)"
            )

    # ── directory scaffolding ─────────────────────────────────────────
    def _ensure_dirs(self) -> None:
        """Create cache/ and all dist/EFI/ subdirectories."""
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        for sub in EFI_SUBDIRS:
            (self.dist_dir / sub).mkdir(parents=True, exist_ok=True)

    # ── GitHub API helpers ────────────────────────────────────────────
    def get_latest_release(self, repo: str) -> dict[str, Any]:
        """Query ``/repos/{repo}/releases/latest`` and return the JSON payload.

        Handles 403 rate-limiting with a retry wait.

        Parameters
        ----------
        repo:
            GitHub ``owner/repo`` slug, e.g. ``"acidanthera/OpenCorePkg"``.

        Returns
        -------
        dict
            The full JSON response from the GitHub Releases API.

        Raises
        ------
        requests.HTTPError
            If the request fails after retries.
        """
        url = f"{GITHUB_API}/repos/{repo}/releases/latest"
        for attempt in range(1, MAX_RETRIES + 1):
            resp = self.session.get(url, timeout=30)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code in (403, 429):
                wait = RATE_LIMIT_WAIT * attempt
                console.print(
                    f"[yellow]⏳ Rate-limited on {repo} – waiting {wait:.0f}s "
                    f"(attempt {attempt}/{MAX_RETRIES})[/]"
                )
                time.sleep(wait)
                continue
            resp.raise_for_status()

        # Final attempt – raise on failure
        resp = self.session.get(url, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def _get_all_releases(self, repo: str) -> list[dict[str, Any]]:
        """Return the first page of releases (up to 30) for *repo*."""
        url = f"{GITHUB_API}/repos/{repo}/releases"
        for attempt in range(1, MAX_RETRIES + 1):
            resp = self.session.get(url, params={"per_page": 30}, timeout=30)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code in (403, 429):
                wait = RATE_LIMIT_WAIT * attempt
                console.print(
                    f"[yellow]⏳ Rate-limited on {repo} – waiting {wait:.0f}s "
                    f"(attempt {attempt}/{MAX_RETRIES})[/]"
                )
                time.sleep(wait)
                continue
            resp.raise_for_status()

        resp = self.session.get(url, params={"per_page": 30}, timeout=30)
        resp.raise_for_status()
        return resp.json()

    # ── file download ─────────────────────────────────────────────────
    def download_file(self, url: str, dest: Path) -> Path:
        """Download *url* to *dest* with a ``rich`` progress bar.

        If *dest* already exists the download is skipped (cache hit).

        Parameters
        ----------
        url:
            Direct download URL.
        dest:
            Local filesystem path where the file should be saved.

        Returns
        -------
        Path
            The *dest* path (for chaining).
        """
        if dest.exists():
            console.print(f"  [dim]↺ cached:[/] {dest.name}")
            return dest

        dest.parent.mkdir(parents=True, exist_ok=True)

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = self.session.get(
                    url,
                    stream=True,
                    timeout=60,
                    headers={"Accept": "application/octet-stream"},
                )
                if resp.status_code in (403, 429):
                    wait = RATE_LIMIT_WAIT * attempt
                    console.print(
                        f"  [yellow]⏳ Rate-limited – waiting {wait:.0f}s[/]"
                    )
                    time.sleep(wait)
                    continue
                resp.raise_for_status()

                total = int(resp.headers.get("content-length", 0))
                with Progress(
                    SpinnerColumn(),
                    TextColumn("[bold blue]{task.fields[filename]}"),
                    BarColumn(bar_width=30),
                    DownloadColumn(),
                    TransferSpeedColumn(),
                    TimeRemainingColumn(),
                    console=console,
                    transient=True,
                ) as progress:
                    task = progress.add_task(
                        "Downloading",
                        total=total or None,
                        filename=dest.name,
                    )
                    tmp = dest.with_suffix(dest.suffix + ".part")
                    with open(tmp, "wb") as fh:
                        for chunk in resp.iter_content(chunk_size=1024 * 64):
                            fh.write(chunk)
                            progress.advance(task, len(chunk))

                tmp.rename(dest)
                console.print(f"  [green]✓[/] {dest.name}")
                return dest

            except requests.ConnectionError as exc:
                if attempt < MAX_RETRIES:
                    console.print(
                        f"  [yellow]⚠ Connection error – retry "
                        f"{attempt}/{MAX_RETRIES}[/]"
                    )
                    time.sleep(RETRY_BACKOFF * attempt)
                else:
                    raise RuntimeError(
                        f"Failed to download {url} after {MAX_RETRIES} retries"
                    ) from exc

        raise RuntimeError(f"Failed to download {url} after {MAX_RETRIES} retries")

    # ── release-zip helpers ───────────────────────────────────────────
    def _find_release_asset(
        self,
        release: dict[str, Any],
        pattern: str,
    ) -> dict[str, Any] | None:
        """Find the first asset whose name matches *pattern* (regex, case-insensitive)."""
        regex = re.compile(pattern, re.IGNORECASE)
        for asset in release.get("assets", []):
            if regex.search(asset["name"]):
                return asset
        return None

    def download_and_extract_kext(
        self,
        repo: str,
        kext_names: list[str],
        *,
        asset_pattern: str | None = None,
        release: dict[str, Any] | None = None,
    ) -> None:
        """Download the latest RELEASE zip from *repo* and extract named kexts.

        Parameters
        ----------
        repo:
            GitHub ``owner/repo`` slug.
        kext_names:
            List of kext directory names to extract, e.g. ``["Lilu.kext"]``.
        asset_pattern:
            Optional regex to match the desired asset name.  Defaults to
            ``RELEASE`` (case-insensitive) zip file.
        release:
            Pre-fetched release dict; if ``None`` the latest release is queried.
        """
        if release is None:
            release = self.get_latest_release(repo)
        tag = release.get("tag_name", "unknown")
        console.print(f"  [cyan]→[/] {repo} [dim]{tag}[/]")

        # Determine asset pattern
        if asset_pattern is None:
            asset_pattern = r"RELEASE.*\.zip$"

        asset = self._find_release_asset(release, asset_pattern)
        if asset is None:
            # Fallback: any zip
            asset = self._find_release_asset(release, r"\.zip$")
        if asset is None:
            console.print(f"  [red]✗ No matching zip found in {repo} {tag}[/]")
            return

        zip_path = self.cache_dir / asset["name"]
        self.download_file(asset["browser_download_url"], zip_path)

        kexts_dir = self.dist_dir / "EFI" / "OC" / "Kexts"
        with zipfile.ZipFile(zip_path) as zf:
            for kext_name in kext_names:
                if _extract_directory_from_zip(zf, kext_name, kexts_dir):
                    console.print(f"    [green]✓[/] {kext_name}")
                else:
                    console.print(f"    [red]✗ {kext_name} not found in zip[/]")

    # ── individual component methods ──────────────────────────────────
    def _download_opencore(self) -> None:
        """Download OpenCorePkg RELEASE and extract core EFI files + ocvalidate."""
        console.print(
            Panel("[bold]1/13  OpenCorePkg[/]", style="blue", expand=False)
        )
        release = self.get_latest_release("acidanthera/OpenCorePkg")
        tag = release.get("tag_name", "unknown")
        console.print(f"  [cyan]→[/] acidanthera/OpenCorePkg [dim]{tag}[/]")

        asset = self._find_release_asset(release, r"OpenCore-.*-RELEASE\.zip$")
        if asset is None:
            console.print("[red]✗ Could not find OpenCorePkg RELEASE zip[/]")
            return

        zip_path = self.cache_dir / asset["name"]
        self.download_file(asset["browser_download_url"], zip_path)

        # Extraction map: (search-suffix in zip) → (destination path relative to dist/)
        extraction_map: dict[str, str] = {
            "BOOTx64.efi": "EFI/BOOT/BOOTx64.efi",
            "OpenCore.efi": "EFI/OC/OpenCore.efi",
            "OpenRuntime.efi": "EFI/OC/Drivers/OpenRuntime.efi",
            "ResetNvramEntry.efi": "EFI/OC/Drivers/ResetNvramEntry.efi",
            "Sample.plist": "EFI/OC/Sample.plist",
        }
        # ocvalidate needs to match the path suffix exactly
        ocvalidate_suffix = "Utilities/ocvalidate/ocvalidate.exe"

        with zipfile.ZipFile(zip_path) as zf:
            # --- standard file extraction ---
            for search_name, dest_rel in extraction_map.items():
                # For BOOTx64.efi & OpenCore.efi we want the X64 variant
                # Try X64 path first, then fall back to basename search
                member = None
                if search_name in ("BOOTx64.efi", "OpenCore.efi"):
                    member = _find_path_in_zip(zf, f"X64/EFI/BOOT/{search_name}") \
                             or _find_path_in_zip(zf, f"X64/EFI/OC/{search_name}") \
                             or _find_path_in_zip(zf, f"X64/{search_name}")
                if member is None:
                    member = _find_in_zip(zf, search_name)
                if member is None:
                    console.print(f"    [red]✗ {search_name} not found in zip[/]")
                    continue
                dest = self.dist_dir / dest_rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, open(dest, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                console.print(f"    [green]✓[/] {dest_rel}")

            # --- ocvalidate ---
            member = _find_path_in_zip(zf, ocvalidate_suffix)
            if member is None:
                member = _find_in_zip(zf, "ocvalidate.exe")
            if member is not None:
                dest = self.dist_dir / "ocvalidate.exe"
                with zf.open(member) as src, open(dest, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                console.print("    [green]✓[/] ocvalidate.exe → dist/")
            else:
                console.print("    [yellow]⚠ ocvalidate.exe not found in zip[/]")

            # --- macrecovery ---
            macrecovery_dir = self.cache_dir / "macrecovery"
            macrecovery_dir.mkdir(parents=True, exist_ok=True)
            for m in zf.namelist():
                if "utilities/macrecovery/" in m.lower() and not m.endswith("/"):
                    fname = Path(m).name
                    with zf.open(m) as src, open(macrecovery_dir / fname, "wb") as dst:
                        shutil.copyfileobj(src, dst)

    def _download_hfsplus(self) -> None:
        """Download HfsPlus.efi from OcBinaryData (raw file, not a release)."""
        console.print(
            Panel("[bold]2/13  HfsPlus.efi (OcBinaryData)[/]", style="blue", expand=False)
        )
        url = f"{RAW_GITHUB}/acidanthera/OcBinaryData/master/Drivers/HfsPlus.efi"
        dest = self.dist_dir / "EFI" / "OC" / "Drivers" / "HfsPlus.efi"
        # We cache in cache/ as well
        cached = self.cache_dir / "HfsPlus.efi"
        self.download_file(url, cached)
        shutil.copy2(cached, dest)
        console.print(f"    [green]✓[/] EFI/OC/Drivers/HfsPlus.efi")

    def _download_lilu(self) -> None:
        """Download Lilu.kext from acidanthera/Lilu."""
        console.print(
            Panel("[bold]3/13  Lilu.kext[/]", style="blue", expand=False)
        )
        self.download_and_extract_kext("acidanthera/Lilu", ["Lilu.kext"])

    def _download_virtualsmc(self) -> None:
        """Download VirtualSMC kexts from acidanthera/VirtualSMC."""
        console.print(
            Panel(
                "[bold]4/13  VirtualSMC (VirtualSMC + SMCBatteryManager + SMCProcessor)[/]",
                style="blue",
                expand=False,
            )
        )
        self.download_and_extract_kext(
            "acidanthera/VirtualSMC",
            ["VirtualSMC.kext", "SMCBatteryManager.kext", "SMCProcessor.kext"],
        )

    def _download_applealc(self) -> None:
        """Download AppleALC.kext from acidanthera/AppleALC."""
        console.print(
            Panel("[bold]5/13  AppleALC.kext[/]", style="blue", expand=False)
        )
        self.download_and_extract_kext("acidanthera/AppleALC", ["AppleALC.kext"])

    def _download_voodoops2(self) -> None:
        """Download VoodooPS2Controller.kext from acidanthera/VoodooPS2."""
        console.print(
            Panel("[bold]6/13  VoodooPS2Controller.kext[/]", style="blue", expand=False)
        )
        self.download_and_extract_kext(
            "acidanthera/VoodooPS2",
            ["VoodooPS2Controller.kext"],
        )

    def _download_ecenabler(self) -> None:
        """Download ECEnabler.kext from 1Revenger1/ECEnabler."""
        console.print(
            Panel("[bold]7/13  ECEnabler.kext[/]", style="blue", expand=False)
        )
        self.download_and_extract_kext(
            "1Revenger1/ECEnabler",
            ["ECEnabler.kext"],
            asset_pattern=r"\.zip$",
        )

    def _download_nvmefix(self) -> None:
        """Download NVMeFix.kext from acidanthera/NVMeFix."""
        console.print(
            Panel("[bold]8/13  NVMeFix.kext[/]", style="blue", expand=False)
        )
        self.download_and_extract_kext("acidanthera/NVMeFix", ["NVMeFix.kext"])

    def _download_whateverxe(self) -> None:
        """Download WhateverXe.kext from b00t0x/WhateverXe."""
        console.print(
            Panel("[bold]9/13  WhateverXe.kext[/]", style="blue", expand=False)
        )
        self.download_and_extract_kext(
            "b00t0x/WhateverXe",
            ["WhateverXe.kext"],
            asset_pattern=r"\.zip$",
        )

    def _download_amfipass(self) -> None:
        """Download AMFIPass.kext (Mod v1.4.1 build required by WhateverXe)."""
        console.print(
            Panel("[bold]10/13  AMFIPass.kext (Mod v1.4.1)[/]", style="blue", expand=False)
        )
        try:
            self.download_and_extract_kext(
                "kaoskinkae/AMFIPass",
                ["AMFIPass.kext"],
                asset_pattern=r"AMFIPass.*\.zip$",
            )
        except Exception:
            console.print("  [yellow]⚠ Primary AMFIPass download failed, trying direct v1.4.1 URL…[/]")
            try:
                direct_url = "https://github.com/kaoskinkae/AMFIPass/releases/download/v1.4.1/AMFIPass.kext.zip"
                cached = self.cache_dir / "AMFIPass.kext.zip"
                self.download_file(direct_url, cached)
                kexts_dir = self.dist_dir / "EFI" / "OC" / "Kexts"
                with zipfile.ZipFile(cached) as zf:
                    if _extract_directory_from_zip(zf, "AMFIPass.kext", kexts_dir):
                        console.print("    [green]✓[/] AMFIPass.kext")
                    else:
                        zf.extractall(kexts_dir)
            except Exception as exc:
                console.print(f"  [red]✗ Could not download AMFIPass.kext: {exc}[/]")

    def _download_voodooi2c(self) -> None:
        """Download VoodooI2C.kext and VoodooI2CHID.kext from VoodooI2C/VoodooI2C."""
        console.print(
            Panel(
                "[bold]11/13  VoodooI2C (VoodooI2C + VoodooI2CHID)[/]",
                style="blue",
                expand=False,
            )
        )
        self.download_and_extract_kext(
            "VoodooI2C/VoodooI2C",
            ["VoodooI2C.kext", "VoodooI2CHID.kext"],
            asset_pattern=r"\.zip$",
        )

    def _download_airportitlwm(self) -> None:
        """Download AirportItlwm.kext from OpenIntelWireless/itlwm.

        Attempts to find a Sequoia or Sonoma build; falls back to latest.
        """
        console.print(
            Panel("[bold]12/13  AirportItlwm.kext[/]", style="blue", expand=False)
        )
        release = self.get_latest_release("OpenIntelWireless/itlwm")
        tag = release.get("tag_name", "unknown")
        console.print(f"  [cyan]→[/] OpenIntelWireless/itlwm [dim]{tag}[/]")

        # Prefer Sequoia > Sonoma > any AirportItlwm zip
        asset: dict[str, Any] | None = None
        for pattern in (
            r"AirportItlwm.*Sequoia.*\.zip$",
            r"AirportItlwm.*Sonoma.*\.zip$",
            r"AirportItlwm.*14.*\.zip$",         # macOS 14 = Sonoma
            r"AirportItlwm.*15.*\.zip$",         # macOS 15 = Sequoia
            r"AirportItlwm.*\.zip$",
        ):
            asset = self._find_release_asset(release, pattern)
            if asset is not None:
                break

        if asset is None:
            console.print("  [red]✗ No AirportItlwm zip found[/]")
            return

        console.print(f"  [dim]asset:[/] {asset['name']}")
        zip_path = self.cache_dir / asset["name"]
        self.download_file(asset["browser_download_url"], zip_path)

        kexts_dir = self.dist_dir / "EFI" / "OC" / "Kexts"
        with zipfile.ZipFile(zip_path) as zf:
            if _extract_directory_from_zip(zf, "AirportItlwm.kext", kexts_dir):
                console.print("    [green]✓[/] AirportItlwm.kext")
            else:
                # Some releases put the kext at the zip root – try extracting everything
                console.print(
                    "    [yellow]⚠ AirportItlwm.kext directory not found; "
                    "extracting all contents…[/]"
                )
                zf.extractall(kexts_dir)

    def _download_intelbluetoothfirmware(self) -> None:
        """Download IntelBluetoothFirmware.kext from OpenIntelWireless/IntelBluetoothFirmware."""
        console.print(
            Panel(
                "[bold]13/13  IntelBluetoothFirmware.kext[/]",
                style="blue",
                expand=False,
            )
        )
        self.download_and_extract_kext(
            "OpenIntelWireless/IntelBluetoothFirmware",
            ["IntelBluetoothFirmware.kext"],
            asset_pattern=r"\.zip$",
        )

    # ── WhateverGreen assertion ───────────────────────────────────────
    def verify_no_whatevergreen(self) -> None:
        """Abort with a hard error if ``WhateverGreen.kext`` is present in dist.

        This build uses **WhateverXe** instead of WhateverGreen.
        The two are mutually exclusive and WhateverGreen must NEVER be included.

        Raises
        ------
        AssertionError
            If ``WhateverGreen.kext`` is found anywhere under ``dist/EFI/OC/Kexts/``.
        """
        kexts_dir = self.dist_dir / "EFI" / "OC" / "Kexts"
        wg_path = kexts_dir / "WhateverGreen.kext"
        if wg_path.exists():
            # Immediately remove to prevent accidental use
            shutil.rmtree(wg_path, ignore_errors=True)
            console.print(
                "[bold red]"
                "╔══════════════════════════════════════════════════════════╗\n"
                "║  FATAL: WhateverGreen.kext detected – REMOVED & ABORT  ║\n"
                "╚══════════════════════════════════════════════════════════╝"
                "[/]"
            )
            raise AssertionError(
                "WhateverGreen.kext was found in dist/EFI/OC/Kexts/. "
                "This build must NEVER include WhateverGreen. "
                "The offending kext has been deleted."
            )

        # Also scan for any lingering reference
        for item in kexts_dir.rglob("*"):
            if "whatevergreen" in item.name.lower():
                console.print(
                    f"[red]✗ Suspicious file related to WhateverGreen: {item}[/]"
                )
                raise AssertionError(
                    f"WhateverGreen-related file found: {item}"
                )

        console.print("[green]✓[/] No WhateverGreen.kext detected (good!)")

    # ── orchestrator ──────────────────────────────────────────────────
    def download_all(self, hardware_report: dict[str, Any] | None = None) -> None:
        """Orchestrate all component downloads.

        Parameters
        ----------
        hardware_report:
            Optional hardware report dict (reserved for future conditional
            downloads).  Currently unused – all components are downloaded.
        """
        console.print()
        console.print(
            Panel(
                "[bold green]Tiger Lake EFI Builder – Component Downloader[/]\n"
                "Fetching latest releases from GitHub…",
                title="⬇  Downloader",
                border_style="green",
            )
        )
        console.print()

        self._ensure_dirs()

        steps: list[tuple[str, callable]] = [
            ("OpenCorePkg",              self._download_opencore),
            ("HfsPlus.efi",              self._download_hfsplus),
            ("Lilu.kext",                self._download_lilu),
            ("VirtualSMC kexts",         self._download_virtualsmc),
            ("AppleALC.kext",            self._download_applealc),
            ("VoodooPS2Controller.kext", self._download_voodoops2),
            ("ECEnabler.kext",           self._download_ecenabler),
            ("NVMeFix.kext",             self._download_nvmefix),
            ("WhateverXe.kext",          self._download_whateverxe),
            ("AMFIPass.kext",            self._download_amfipass),
            ("VoodooI2C kexts",          self._download_voodooi2c),
            ("AirportItlwm.kext",        self._download_airportitlwm),
            ("IntelBluetoothFirmware",   self._download_intelbluetoothfirmware),
        ]

        failed: list[str] = []
        for name, func in steps:
            try:
                func()
            except Exception as exc:
                console.print(f"  [bold red]✗ {name} failed: {exc}[/]")
                failed.append(name)

        # ── WhateverGreen guard ───────────────────────────────────────
        self.verify_no_whatevergreen()

        # ── summary ──────────────────────────────────────────────────
        console.print()
        kexts_dir = self.dist_dir / "EFI" / "OC" / "Kexts"
        kext_dirs = sorted(
            p.name for p in kexts_dir.iterdir() if p.is_dir() and p.suffix == ".kext"
        )

        summary = Table(title="Downloaded Kexts", show_lines=False)
        summary.add_column("#", style="dim", width=4)
        summary.add_column("Kext", style="bold")
        for i, kext in enumerate(kext_dirs, 1):
            summary.add_row(str(i), kext)
        console.print(summary)

        if failed:
            console.print(
                f"\n[bold yellow]⚠ {len(failed)} component(s) failed:[/] "
                + ", ".join(failed)
            )
        else:
            console.print("\n[bold green]✓ All components downloaded successfully![/]")

        console.print()


# ──────────────────────────────────────────────────────────────────────
# CLI entry point
# ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    base = Path(__file__).resolve().parent.parent
    dl = GitHubDownloader(base_dir=base)
    dl.download_all()
