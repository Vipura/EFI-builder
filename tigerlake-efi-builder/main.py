#!/usr/bin/env python3
"""
TigerLake-EFI-Builder
=====================
Automated OpenCore EFI folder generator for Lenovo IdeaPad Slim 5 (82FG)
with 11th Gen Intel Core i5-1135G7 and Intel Iris Xe Graphics.

This tool scans the host hardware, downloads required OpenCore components,
compiles ACPI SSDTs, generates config.plist, and outputs a validated EFI folder.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich import box

console = Console(legacy_windows=False)

# Project paths
PROJECT_ROOT = Path(__file__).parent.resolve()
DIST_DIR = PROJECT_ROOT / "dist"
EFI_DIR = DIST_DIR / "EFI"
EFI_OC_DIR = EFI_DIR / "OC"
CACHE_DIR = PROJECT_ROOT / "cache"
TEMPLATES_DIR = PROJECT_ROOT / "templates" / "acpi_dsl"


def print_banner():
    """Display the application banner."""
    banner = Text()
    banner.append("╔══════════════════════════════════════════════════════════╗\n", style="bold cyan")
    banner.append("║         TigerLake-EFI-Builder v1.0.0                    ║\n", style="bold cyan")
    banner.append("║  OpenCore EFI Generator for Lenovo IdeaPad Slim 5 82FG  ║\n", style="bold cyan")
    banner.append("║  CPU: i5-1135G7 | GPU: Intel Iris Xe (9A49)            ║\n", style="bold cyan")
    banner.append("╚══════════════════════════════════════════════════════════╝", style="bold cyan")
    console.print(banner)
    console.print()


def ensure_directories():
    """Create the output directory structure."""
    dirs = [
        DIST_DIR,
        CACHE_DIR,
        EFI_DIR / "BOOT",
        EFI_OC_DIR / "ACPI",
        EFI_OC_DIR / "Drivers",
        EFI_OC_DIR / "Kexts",
        EFI_OC_DIR / "Resources",
        EFI_OC_DIR / "Tools",
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)


def step_hardware_scan() -> dict:
    """Step 1: Scan host hardware and display report."""
    console.rule("[bold blue]Step 1/5: Hardware Scan[/bold blue]")
    console.print()

    from modules.hw_scanner import scan, display_report

    report = scan()
    display_report(report)

    # Save hardware report
    report_path = PROJECT_ROOT / "hardware_report.json"
    # Convert bytes to hex strings for JSON serialization
    serializable = json.loads(json.dumps(report, default=str))
    with open(report_path, "w") as f:
        json.dump(serializable, f, indent=2)
    console.print(f"\n[green]✓[/green] Hardware report saved to [cyan]{report_path}[/cyan]")
    console.print()
    return report


def step_download(hardware_report: dict):
    """Step 2: Download OpenCore binaries and kexts."""
    console.rule("[bold blue]Step 2/5: Download OpenCore Components[/bold blue]")
    console.print()

    from modules.downloader import GitHubDownloader

    downloader = GitHubDownloader(
        cache_dir=CACHE_DIR,
        dist_dir=DIST_DIR,
    )
    downloader.download_all(hardware_report)

    # Strict WhateverGreen check
    downloader.verify_no_whatevergreen()

    console.print("\n[green]✓[/green] All components downloaded successfully")
    console.print()


def step_acpi_build(hardware_report: dict):
    """Step 3: Compile ACPI SSDTs."""
    console.rule("[bold blue]Step 3/5: ACPI / SSDT Compilation[/bold blue]")
    console.print()

    from modules.acpi_builder import build

    build(
        hardware_report=hardware_report,
        templates_dir=TEMPLATES_DIR,
        output_dir=EFI_OC_DIR / "ACPI",
        cache_dir=CACHE_DIR,
    )

    console.print("\n[green]✓[/green] All SSDTs compiled successfully")
    console.print()


def step_config_plist(hardware_report: dict):
    """Step 4: Generate config.plist."""
    console.rule("[bold blue]Step 4/5: config.plist Generation[/bold blue]")
    console.print()

    from modules.smbios_gen import SMBIOSGenerator
    from modules.plist_config import ConfigPlistBuilder

    # Generate SMBIOS data
    console.print("[cyan]Generating SMBIOS identity (MacBookAir9,1)...[/cyan]")
    smbios = SMBIOSGenerator()
    smbios_data = smbios.generate()
    smbios.display(smbios_data)
    console.print()

    # Build config.plist
    sample_plist = EFI_OC_DIR / "Sample.plist"
    if not sample_plist.exists():
        console.print("[red]✗ Sample.plist not found! Download step may have failed.[/red]")
        sys.exit(1)

    builder = ConfigPlistBuilder(
        sample_plist_path=sample_plist,
        efi_oc_path=EFI_OC_DIR,
        smbios_data=smbios_data,
        hardware_report=hardware_report,
    )
    builder.build()

    console.print("\n[green]✓[/green] config.plist generated successfully")
    console.print()


def step_validate():
    """Step 5: Run ocvalidate against the generated config.plist."""
    console.rule("[bold blue]Step 5/5: Validation[/bold blue]")
    console.print()

    ocvalidate = DIST_DIR / "ocvalidate.exe"
    config_plist = EFI_OC_DIR / "config.plist"

    if not ocvalidate.exists():
        console.print("[yellow]⚠ ocvalidate.exe not found, skipping validation.[/yellow]")
        console.print("[dim]  You can validate manually later with:[/dim]")
        console.print(f"[dim]  {ocvalidate} {config_plist}[/dim]")
        return

    if not config_plist.exists():
        console.print("[red]✗ config.plist not found![/red]")
        sys.exit(1)

    console.print(f"[cyan]Running ocvalidate...[/cyan]")
    result = subprocess.run(
        [str(ocvalidate), str(config_plist)],
        capture_output=True,
        text=True,
    )

    # Display output
    if result.stdout:
        for line in result.stdout.strip().splitlines():
            if "no issues found" in line.lower() or "valid" in line.lower():
                console.print(f"  [green]{line}[/green]")
            elif "error" in line.lower() or "warning" in line.lower():
                console.print(f"  [yellow]{line}[/yellow]")
            else:
                console.print(f"  {line}")

    if result.stderr:
        for line in result.stderr.strip().splitlines():
            console.print(f"  [red]{line}[/red]")

    if result.returncode == 0:
        console.print("\n[green]✓[/green] config.plist validation passed!")
    else:
        console.print(f"\n[yellow]⚠ ocvalidate exited with code {result.returncode}[/yellow]")
        console.print("[dim]  Review the output above and adjust config.plist if needed.[/dim]")

    console.print()


def step_download_recovery():
    """Optional: Download macOS recovery image using macrecovery.py."""
    console.rule("[bold blue]Optional: macOS Recovery Download[/bold blue]")
    console.print()

    recovery_dir = DIST_DIR / "com.apple.recovery.boot"
    recovery_dir.mkdir(parents=True, exist_ok=True)

    # Check if macrecovery.py exists in OpenCore utilities
    macrecovery_paths = [
        CACHE_DIR / "macrecovery" / "macrecovery.py",
        CACHE_DIR / "OpenCorePkg" / "Utilities" / "macrecovery" / "macrecovery.py",
    ]

    macrecovery = None
    for p in macrecovery_paths:
        if p.exists():
            macrecovery = p
            break

    if macrecovery is None:
        # Try to download macrecovery.py from OpenCorePkg repo
        console.print("[cyan]Downloading macrecovery.py from OpenCorePkg...[/cyan]")
        import requests
        url = "https://raw.githubusercontent.com/acidanthera/OpenCorePkg/master/Utilities/macrecovery/macrecovery.py"
        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            macrecovery = CACHE_DIR / "macrecovery.py"
            macrecovery.write_text(resp.text, encoding="utf-8")
            console.print(f"[green]✓[/green] Downloaded macrecovery.py")
        except Exception as e:
            console.print(f"[red]✗ Failed to download macrecovery.py: {e}[/red]")
            console.print("[dim]  You can manually download the macOS recovery image.[/dim]")
            return

    # Run macrecovery to download Sonoma recovery
    # Board ID for MacBookAir9,1
    console.print("[cyan]Downloading macOS Sonoma recovery image...[/cyan]")
    console.print("[dim]  This may take several minutes depending on your connection.[/dim]")
    console.print()

    cmd = [
        sys.executable, str(macrecovery),
        "-b", "Mac-0CFF9C7C2B63DF8D",  # MacBookAir9,1 board-id
        "-m", "00000000000000000",
        "-os", "latest",
        "download",
        "-o", str(recovery_dir),
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if result.stdout:
            console.print(result.stdout)
        if result.returncode == 0:
            # Check for downloaded files
            base_system = recovery_dir / "BaseSystem.dmg"
            chunklist = recovery_dir / "BaseSystem.chunklist"
            if base_system.exists():
                console.print(f"[green]✓[/green] BaseSystem.dmg downloaded ({base_system.stat().st_size / 1024 / 1024:.0f} MB)")
            if chunklist.exists():
                console.print(f"[green]✓[/green] BaseSystem.chunklist downloaded")
        else:
            console.print(f"[yellow]⚠ macrecovery exited with code {result.returncode}[/yellow]")
            if result.stderr:
                console.print(f"[red]{result.stderr}[/red]")
    except subprocess.TimeoutExpired:
        console.print("[yellow]⚠ Recovery download timed out (30 min limit)[/yellow]")
    except Exception as e:
        console.print(f"[red]✗ Failed to run macrecovery: {e}[/red]")

    console.print()


def print_summary():
    """Print the final summary with EFI folder location."""
    efi_path = EFI_DIR.resolve()
    console.print(Panel(
        f"[bold green]EFI folder successfully generated![/bold green]\n\n"
        f"  📁 Location: [cyan]{efi_path}[/cyan]\n\n"
        f"  [dim]Copy the EFI folder to the EFI partition of your USB installer.[/dim]\n"
        f"  [dim]Ensure you verify the SMBIOS serial at [/dim][blue]https://checkcoverage.apple.com[/blue]\n"
        f"  [dim](serial should return 'invalid' for a safe Hackintosh SMBIOS).[/dim]\n\n"
        f"  [bold yellow]⚠ Important Notes:[/bold yellow]\n"
        f"  • WhateverXe.kext is used instead of WhateverGreen.kext for Tiger Lake Xe\n"
        f"  • The Nvidia MX450 dGPU is disabled via SSDT-dGPU-Off\n"
        f"  • CPUID is spoofed from Tiger Lake → Ice Lake for XNU compatibility\n"
        f"  • DVMT framebuffer patches bypass the locked 60MB Lenovo BIOS limit",
        title="[bold]Build Complete[/bold]",
        box=box.DOUBLE,
        border_style="green",
    ))


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        prog="TigerLake-EFI-Builder",
        description="Automated OpenCore EFI builder for Lenovo IdeaPad Slim 5 (82FG) "
                    "with 11th Gen Intel Core i5-1135G7 and Intel Iris Xe Graphics.",
    )
    parser.add_argument(
        "--download-recovery",
        action="store_true",
        help="Download macOS recovery image (BaseSystem.dmg) via macrecovery.py",
    )
    parser.add_argument(
        "--skip-hw-scan",
        action="store_true",
        help="Skip hardware scanning (use defaults for Lenovo IdeaPad Slim 5 82FG)",
    )
    parser.add_argument(
        "--github-token",
        type=str,
        default=None,
        help="GitHub personal access token for higher API rate limits",
    )
    args = parser.parse_args()

    # Set GitHub token if provided
    if args.github_token:
        import os
        os.environ["GITHUB_TOKEN"] = args.github_token

    print_banner()

    # Create directory structure
    ensure_directories()

    # Step 1: Hardware scan
    if args.skip_hw_scan:
        console.print("[yellow]⚠ Hardware scan skipped, using defaults for Lenovo IdeaPad Slim 5 82FG[/yellow]")
        hardware_report = {
            "cpu": {"name": "11th Gen Intel(R) Core(TM) i5-1135G7 @ 2.40GHz", "cores": 4, "threads": 8, "is_tiger_lake": True},
            "igpu": {"name": "Intel Iris Xe Graphics", "pci_id": "VEN_8086&DEV_9A49", "device_id": "9A49", "verified": True},
            "dgpu": {"name": "NVIDIA GeForce MX450", "pci_id": "VEN_10DE", "acpi_path": r"\_SB.PC00.PEG0.PEGP", "needs_disable": True},
            "input": {
                "keyboard": {"type": "PS/2", "pnp_id": "ACPI\\PNP0303"},
                "trackpad": {"type": "I2C HID", "pnp_id": "ACPI\\SYNA3602", "is_i2c": True},
            },
            "network": {
                "wifi": {"name": "Intel Wi-Fi 6 AX201", "pci_id": "VEN_8086&DEV_A0F0", "is_intel": True},
                "bluetooth": {"name": "Intel Bluetooth", "usb_id": "USB\\VID_8087&PID_0026"},
            },
            "audio": {"name": "Realtek High Definition Audio", "codec_id": "VEN_10EC&DEV_0257", "alc_layout_id": 11},
            "storage": {"nvme_detected": True, "controller": "Samsung NVMe"},
            "machine": {"manufacturer": "LENOVO", "model": "82FG", "bios_version": "Unknown"},
        }
        console.print()
    else:
        hardware_report = step_hardware_scan()

    # Step 2: Download components
    step_download(hardware_report)

    # Step 3: Compile SSDTs
    step_acpi_build(hardware_report)

    # Step 4: Generate config.plist
    step_config_plist(hardware_report)

    # Step 5: Validate
    step_validate()

    # Optional: Download recovery
    if args.download_recovery:
        step_download_recovery()

    # Final summary
    print_summary()


if __name__ == "__main__":
    main()

