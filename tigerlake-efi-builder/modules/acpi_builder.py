#!/usr/bin/env python3
"""
acpi_builder.py — ACPI SSDT Compiler & Builder for Tiger Lake EFI
=================================================================

Downloads iasl.exe (Intel ACPI Source Language compiler), patches the
dGPU-Off SSDT with hardware-specific ACPI paths, and compiles all
DSL templates into AML bytecode for OpenCore injection.

Workflow
--------
1. ``ensure_iasl()``        – download iasl.exe to ``cache/`` if missing
2. ``update_dgpu_ssdt()``   – patch dGPU-Off template with detected paths
3. ``compile_all()``        – compile every ``.dsl`` → ``.aml``
4. ``build()``              – orchestrator that runs steps 2-3 in order
"""

from __future__ import annotations

import io
import re
import shutil
import subprocess
import sys
import textwrap
import zipfile
from pathlib import Path
from typing import Any

import requests
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = PROJECT_ROOT / "templates" / "acpi_dsl"
CACHE_DIR = PROJECT_ROOT / "cache"
DIST_ACPI_DIR = PROJECT_ROOT / "dist" / "EFI" / "OC" / "ACPI"
IASL_PATH = CACHE_DIR / "iasl.exe"

ACPICA_RELEASES_API = "https://api.github.com/repos/acpica/acpica/releases"
ACPICA_DIRECT_IASL_URL = "https://github.com/open-acpica/acpica/releases/download/20260408/iasl.exe"
CORPNEWT_IASL_ZIP_URL = "https://raw.githubusercontent.com/corpnewt/iasl-legacy/main/iasl-legacy-windows.zip"

# Known dGPU _OFF ACPI paths already present in the template
_KNOWN_DGPU_PATHS: list[str] = [
    r"\_SB.PC00.PEG0.PEGP._OFF",
    r"\_SB.PC00.RP01.PXSX._OFF",
    r"\_SB.PC00.RP05.PXSX._OFF",
    r"\_SB.PCI0.PEG0.PEGP._OFF",
    r"\_SB.PCI0.RP01.PXSX._OFF",
]

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

console = Console(legacy_windows=False)


# ---------------------------------------------------------------------------
# iasl download helpers
# ---------------------------------------------------------------------------


def _download_iasl_from_acpica() -> bytes | None:
    """Try to download iasl.exe from ACPICA GitHub releases.

    Looks for an iasl.exe asset directly or a Windows binary zip asset
    across recent releases. Returns raw bytes or None on failure.
    """
    try:
        console.log("[cyan]Querying ACPICA GitHub releases …[/]")
        resp = requests.get(
            ACPICA_RELEASES_API,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "TigerLake-EFI-Builder/1.0"},
            timeout=30,
        )
        if resp.status_code == 200:
            releases = resp.json()
            if isinstance(releases, list):
                for rel in releases:
                    assets = rel.get("assets", [])
                    # Check for direct iasl.exe first
                    for asset in assets:
                        if asset.get("name", "").lower() == "iasl.exe":
                            console.log(f"[cyan]Found iasl.exe in release {rel.get('tag_name')} …[/]")
                            down = requests.get(asset["browser_download_url"], headers={"User-Agent": "TigerLake-EFI-Builder/1.0"}, timeout=60)
                            if down.status_code == 200 and len(down.content) > 50000:
                                return down.content

                    # Check for zip
                    for asset in assets:
                        name = asset.get("name", "").lower()
                        if ("win" in name or "iasl" in name) and name.endswith(".zip"):
                            console.log(f"[cyan]Downloading {asset['name']} from {rel.get('tag_name')} …[/]")
                            down = requests.get(asset["browser_download_url"], headers={"User-Agent": "TigerLake-EFI-Builder/1.0"}, timeout=60)
                            if down.status_code == 200:
                                with zipfile.ZipFile(io.BytesIO(down.content)) as zf:
                                    for member in zf.namelist():
                                        if member.lower().endswith("iasl.exe"):
                                            console.log(f"[green]Extracted {member} from zip[/]")
                                            return zf.read(member)

        # Fallback to known direct release URL
        console.log("[cyan]Attempting direct download of ACPICA iasl.exe …[/]")
        direct = requests.get(ACPICA_DIRECT_IASL_URL, headers={"User-Agent": "TigerLake-EFI-Builder/1.0"}, timeout=60)
        if direct.status_code == 200 and len(direct.content) > 50000:
            return direct.content

    except Exception as exc:  # noqa: BLE001
        console.log(f"[yellow]ACPICA download failed: {exc}[/]")

    return None


def _download_iasl_from_corpnewt() -> bytes | None:
    """Fallback: download iasl from CorpNewt repo."""
    try:
        console.log("[cyan]Fallback: downloading iasl from CorpNewt …[/]")
        resp = requests.get(CORPNEWT_IASL_ZIP_URL, headers={"User-Agent": "TigerLake-EFI-Builder/1.0"}, timeout=60)
        if resp.status_code == 200:
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                for member in zf.namelist():
                    if member.lower().endswith("iasl.exe"):
                        console.log(f"[green]Extracted {member} from CorpNewt zip[/]")
                        return zf.read(member)
    except Exception as exc:  # noqa: BLE001
        console.log(f"[red]CorpNewt download failed: {exc}[/]")

    return None


def ensure_iasl(cache_dir: Path | None = None) -> Path:
    """Ensure ``iasl.exe`` is available locally, downloading if necessary.

    Parameters
    ----------
    cache_dir:
        Directory to store the cached binary.  Defaults to ``PROJECT_ROOT/cache``.

    Returns
    -------
    Path
        Absolute path to the usable ``iasl.exe``.

    Raises
    ------
    FileNotFoundError
        If the binary cannot be obtained from any source.
    """
    _cache = cache_dir or CACHE_DIR
    iasl = _cache / "iasl.exe"

    if iasl.is_file():
        console.log(f"[green]iasl.exe already cached at [bold]{iasl}[/bold][/]")
        return iasl

    _cache.mkdir(parents=True, exist_ok=True)

    # Attempt primary source first, then fallback
    data = _download_iasl_from_acpica()
    if data is None:
        data = _download_iasl_from_corpnewt()

    if data is None:
        raise FileNotFoundError(
            "Could not download iasl.exe from any source. "
            "Please place iasl.exe manually into: " + str(_cache)
        )

    iasl.write_bytes(data)
    console.log(f"[bold green]✓ iasl.exe saved to {iasl} ({len(data):,} bytes)[/]")
    return iasl


# ---------------------------------------------------------------------------
# dGPU SSDT patching
# ---------------------------------------------------------------------------


def _acpi_path_to_external_name(acpi_path: str) -> str:
    """Convert ``\\_SB.PC00.RP09.PXSX._OFF`` → ``_SB_.PC00.RP09.PXSX._OFF``.

    The ``External`` statement in ASL uses underscored root (``_SB_``)
    notation, while ``CondRefOf`` / call sites use backslash-dot notation.
    """
    # Normalise — strip leading backslash(es) if present
    clean = acpi_path.lstrip("\\").strip()
    parts = clean.split(".")
    # First segment is typically _SB → _SB_ in External declarations
    if parts and parts[0] == "_SB":
        parts[0] = "_SB_"
    return ".".join(parts)


def update_dgpu_ssdt(
    hardware_report: dict[str, Any],
    templates_dir: Path | None = None,
) -> Path:
    """Patch ``SSDT-dGPU-Off.dsl`` with any extra ACPI path from the scanner.

    The hardware report may contain an ``dgpu_acpi_path`` key whose value
    is the ACPI path to the discrete GPU's ``_OFF`` method (e.g.
    ``\\_SB.PC00.RP09.PXSX._OFF``).  If this path is not already covered
    by the template, an ``External`` declaration and a ``CondRefOf`` call
    block are injected.

    Parameters
    ----------
    hardware_report:
        Dict produced by the hardware scanner.
    templates_dir:
        Path to the ``acpi_dsl`` templates folder.

    Returns
    -------
    Path
        Path to the (possibly modified) DSL file.
    """
    tpl_dir = templates_dir or TEMPLATES_DIR
    dsl_path = tpl_dir / "SSDT-dGPU-Off.dsl"

    if not dsl_path.is_file():
        console.log("[red]SSDT-dGPU-Off.dsl template not found![/]")
        raise FileNotFoundError(f"Missing template: {dsl_path}")

    dgpu_path: str | None = hardware_report.get("dgpu_acpi_path") or (
        hardware_report.get("dgpu", {}).get("acpi_path") if isinstance(hardware_report.get("dgpu"), dict) else None
    )

    if not dgpu_path:
        console.log("[dim]No extra dGPU ACPI path in hardware report — template unchanged.[/]")
        return dsl_path

    # Ensure the path ends with ._OFF
    normalised = dgpu_path.strip()
    if not normalised.endswith("._OFF"):
        normalised += "._OFF"

    # Check if this path is already known
    for known in _KNOWN_DGPU_PATHS:
        if known.replace("\\", "").replace("_", "") == normalised.replace("\\", "").replace("_", ""):
            console.log(
                f"[dim]dGPU path [bold]{normalised}[/bold] already in template — skipping.[/]"
            )
            return dsl_path

    console.log(
        f"[yellow]Injecting extra dGPU path: [bold]{normalised}[/bold] into template …[/]"
    )

    dsl_content = dsl_path.read_text(encoding="utf-8")
    ext_name = _acpi_path_to_external_name(normalised)

    # --- Inject External declaration ---
    # Insert before the first blank line after the last External() line
    external_line = f"    External ({ext_name}, MethodObj)"
    # Find the last External line and insert after it
    lines = dsl_content.splitlines(keepends=True)
    last_external_idx = -1
    for idx, line in enumerate(lines):
        if line.strip().startswith("External ("):
            last_external_idx = idx
    if last_external_idx == -1:
        console.log("[red]Could not locate External declarations in template![/]")
        return dsl_path

    lines.insert(last_external_idx + 1, external_line + "\n")

    # --- Inject CondRefOf call block ---
    # Find the last CondRefOf line and insert after it
    last_condref_idx = -1
    for idx, line in enumerate(lines):
        if "CondRefOf" in line:
            last_condref_idx = idx
    if last_condref_idx == -1:
        console.log("[red]Could not locate CondRefOf blocks in template![/]")
        return dsl_path

    # Build the call-site path (backslash-dot notation)
    call_path = normalised if normalised.startswith("\\") else "\\" + normalised
    condref_line = (
        f"            If (CondRefOf ({call_path})) "
        f"{{ {call_path} () }}"
    )
    lines.insert(last_condref_idx + 1, condref_line + "\n")

    dsl_path.write_text("".join(lines), encoding="utf-8")
    console.log(f"[bold green]✓ SSDT-dGPU-Off.dsl patched successfully.[/]")
    return dsl_path


# ---------------------------------------------------------------------------
# Compilation
# ---------------------------------------------------------------------------


def compile_all(
    templates_dir: Path | None = None,
    output_dir: Path | None = None,
    cache_dir: Path | None = None,
) -> list[Path]:
    """Compile every ``.dsl`` file in *templates_dir* into ``.aml`` bytecode.

    Parameters
    ----------
    templates_dir:
        Folder containing ``.dsl`` source files.
    output_dir:
        Destination for compiled ``.aml`` files.  Defaults to
        ``dist/EFI/OC/ACPI/``.
    cache_dir:
        Directory for iasl.exe cache. Defaults to ``CACHE_DIR``.

    Returns
    -------
    list[Path]
        List of successfully compiled ``.aml`` file paths.

    Raises
    ------
    RuntimeError
        If any compilation fails.
    """
    tpl_dir = templates_dir or TEMPLATES_DIR
    out_dir = output_dir or DIST_ACPI_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    iasl = ensure_iasl(cache_dir=cache_dir)

    dsl_files = sorted(tpl_dir.glob("*.dsl"))
    if not dsl_files:
        console.log("[yellow]No .dsl files found in templates directory.[/]")
        return []

    console.print()
    console.rule("[bold cyan]ACPI Compilation[/]")

    results_table = Table(
        title="SSDT Compilation Results",
        show_lines=True,
        title_style="bold white",
    )
    results_table.add_column("Template", style="cyan", min_width=28)
    results_table.add_column("Output", style="green", min_width=28)
    results_table.add_column("Status", justify="center", min_width=10)
    results_table.add_column("Size", justify="right", min_width=10)

    compiled: list[Path] = []
    errors: list[str] = []

    for dsl_file in dsl_files:
        aml_name = dsl_file.stem + ".aml"
        aml_output = out_dir / aml_name

        # iasl -p <output_path_without_ext> <input.dsl>
        # iasl adds .aml extension automatically when using -p
        output_prefix = out_dir / dsl_file.stem

        cmd = [
            str(iasl),
            "-p", str(output_prefix),
            str(dsl_file),
        ]

        console.log(f"[cyan]Compiling [bold]{dsl_file.name}[/bold] …[/]")

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
            )

            if result.returncode != 0:
                err_msg = (
                    result.stderr.strip() or result.stdout.strip() or "Unknown error"
                )
                console.log(
                    f"[red]  ✗ Compilation failed for {dsl_file.name}:[/]\n"
                    f"[dim red]    {err_msg[:300]}[/]"
                )
                errors.append(f"{dsl_file.name}: {err_msg[:200]}")
                results_table.add_row(
                    dsl_file.name,
                    "—",
                    "[bold red]FAIL[/]",
                    "—",
                )
                continue

            if not aml_output.is_file():
                error_detail = f"{aml_name} not found after compilation"
                console.log(f"[red]  ✗ {error_detail}[/]")
                errors.append(f"{dsl_file.name}: {error_detail}")
                results_table.add_row(
                    dsl_file.name,
                    "—",
                    "[bold red]MISSING[/]",
                    "—",
                )
                continue

            size = aml_output.stat().st_size
            compiled.append(aml_output)
            console.log(f"[green]  ✓ {aml_name} ({size:,} bytes)[/]")
            results_table.add_row(
                dsl_file.name,
                aml_name,
                "[bold green]OK[/]",
                f"{size:,} B",
            )

        except subprocess.TimeoutExpired:
            err = f"{dsl_file.name}: compilation timed out"
            console.log(f"[red]  ✗ {err}[/]")
            errors.append(err)
            results_table.add_row(
                dsl_file.name,
                "—",
                "[bold red]TIMEOUT[/]",
                "—",
            )
        except OSError as exc:
            err = f"{dsl_file.name}: {exc}"
            console.log(f"[red]  ✗ {err}[/]")
            errors.append(err)
            results_table.add_row(
                dsl_file.name,
                "—",
                "[bold red]ERROR[/]",
                "—",
            )

    console.print()
    console.print(results_table)
    console.print()

    # Clean up intermediate files iasl may produce (.hex, .src, etc.)
    for suffix in (".hex",):
        for leftover in out_dir.glob(f"*{suffix}"):
            leftover.unlink(missing_ok=True)

    if errors:
        error_summary = "\n".join(f"  • {e}" for e in errors)
        raise RuntimeError(
            f"ACPI compilation failed for {len(errors)} file(s):\n{error_summary}"
        )

    console.print(
        Panel(
            f"[bold green]All {len(compiled)} SSDT(s) compiled successfully![/]\n"
            f"[dim]Output: {out_dir}[/]",
            title="✅ ACPI Build Complete",
            border_style="green",
        )
    )

    return compiled


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def build(
    hardware_report: dict[str, Any],
    templates_dir: Path | None = None,
    output_dir: Path | None = None,
    cache_dir: Path | None = None,
) -> list[Path]:
    """Orchestrate the full ACPI build pipeline.

    1. Patch dGPU-Off SSDT with hardware-specific paths (if any).
    2. Compile all DSL templates into AML bytecode.

    Parameters
    ----------
    hardware_report:
        Hardware scan results dict. Expected keys (all optional):
        - ``dgpu_acpi_path`` (str): ACPI path to discrete GPU ``_OFF`` method.
    templates_dir:
        Path to templates folder.
    output_dir:
        Path to output folder for AML files.
    cache_dir:
        Path to cache folder for iasl.exe.

    Returns
    -------
    list[Path]
        List of compiled ``.aml`` files.
    """
    console.print()
    console.print(
        Panel(
            "[bold]ACPI SSDT Builder — Tiger Lake[/]\n"
            "[dim]Patch → Compile → Package[/]",
            border_style="bright_blue",
        )
    )
    console.print()

    # Step 1: Patch dGPU SSDT if needed
    console.rule("[bold]Step 1 · dGPU SSDT Patch[/]")
    update_dgpu_ssdt(hardware_report, templates_dir=templates_dir)

    # Step 2: Compile all DSL → AML
    console.rule("[bold]Step 2 · Compile DSL → AML[/]")
    compiled = compile_all(
        templates_dir=templates_dir,
        output_dir=output_dir,
        cache_dir=cache_dir,
    )

    return compiled


# ---------------------------------------------------------------------------
# CLI entry-point for standalone testing
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json

    console.print(
        Panel(
            "[bold cyan]ACPI Builder — Standalone Test Mode[/]",
            border_style="cyan",
        )
    )

    # Accept an optional JSON hardware report from a file argument
    hw_report: dict[str, Any] = {}
    if len(sys.argv) > 1:
        report_path = Path(sys.argv[1])
        if report_path.is_file():
            console.log(f"[cyan]Loading hardware report from {report_path} …[/]")
            hw_report = json.loads(report_path.read_text(encoding="utf-8"))
        else:
            console.log(f"[yellow]Report file not found: {report_path} — using empty report.[/]")
    else:
        console.log("[dim]No hardware report file supplied — using defaults.[/]")

    try:
        results = build(hw_report)
        console.print(f"\n[bold green]Build complete — {len(results)} AML file(s) produced.[/]")
        for aml in results:
            console.print(f"  [green]•[/] {aml}")
    except Exception as exc:
        console.print(f"\n[bold red]Build failed:[/] {exc}")
        sys.exit(1)
