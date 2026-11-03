"""
plist_config.py — OpenCore config.plist builder for Tiger Lake laptops.

Loads the OpenCore Sample.plist and patches it with Tiger Lake–specific settings,
kext declarations (in strict dependency order), ACPI entries, SMBIOS identity,
DeviceProperties for iGPU DVMT bypass, CPUID spoofing, and NVRAM boot-args.
"""

from __future__ import annotations

import plistlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import sys
from rich.console import Console
from rich.table import Table

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

console = Console(legacy_windows=False)

# ---------------------------------------------------------------------------
# Canonical kext load order – items are listed in the EXACT sequence they must
# appear in Kernel -> Add.  Entries that live inside a parent kext's PlugIns
# directory are represented as tuples: (parent_bundle, child_bundle).
# ---------------------------------------------------------------------------
_KEXT_ORDER: List[str | Tuple[str, str]] = [
    # --- Tier 1: core frameworks ---
    "Lilu.kext",
    "VirtualSMC.kext",
    "WhateverXe.kext",
    "AMFIPass.kext",
    # --- Tier 2: SMC satellites & misc ---
    "SMCBatteryManager.kext",
    "SMCProcessor.kext",
    "ECEnabler.kext",
    "AppleALC.kext",
    "NVMeFix.kext",
    # --- Tier 3: PS/2 stack ---
    "VoodooPS2Controller.kext",
    ("VoodooPS2Controller.kext", "VoodooPS2Keyboard.kext"),
    ("VoodooPS2Controller.kext", "VoodooPS2Trackpad.kext"),
    ("VoodooPS2Controller.kext", "VoodooPS2Mouse.kext"),
    # --- Tier 4: I2C stack ---
    "VoodooI2CServices.kext",
    "VoodooGPIO.kext",
    "VoodooInput.kext",
    "VoodooI2C.kext",
    "VoodooI2CHID.kext",
    # --- Tier 5: Intel wireless / BT ---
    "IntelBluetoothFirmware.kext",
    "IntelBTPatcher.kext",
    "AirportItlwm.kext",
]


class ConfigPlistBuilder:
    """Builds a patched OpenCore ``config.plist`` from a Sample.plist template.

    Parameters
    ----------
    sample_plist_path:
        Absolute path to the OpenCore ``Sample.plist``.
    efi_oc_path:
        Absolute path to ``dist/EFI/OC/``.
    smbios_data:
        Dictionary produced by :pymod:`smbios_gen` containing identity fields.
    hardware_report:
        Dictionary produced by :pymod:`hw_scanner` with detected hardware info.
    """

    def __init__(
        self,
        sample_plist_path: Path,
        efi_oc_path: Path,
        smbios_data: dict,
        hardware_report: dict,
    ) -> None:
        self.sample_plist_path = Path(sample_plist_path)
        self.efi_oc_path = Path(efi_oc_path)
        self.smbios_data = smbios_data
        self.hardware_report = hardware_report

        self.kexts_path = self.efi_oc_path / "Kexts"
        self.acpi_path = self.efi_oc_path / "ACPI"
        self.config_plist_path = self.efi_oc_path / "config.plist"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build(self) -> Path:
        """Load ``Sample.plist``, apply all Tiger Lake patches, and write
        ``config.plist`` into the OC directory.

        Returns
        -------
        Path
            The absolute path to the generated ``config.plist``.

        Raises
        ------
        FileNotFoundError
            If ``Sample.plist`` cannot be found.
        plistlib.InvalidFileException
            If the sample plist is malformed.
        """
        console.rule("[bold cyan]config.plist Builder[/bold cyan]")

        if not self.sample_plist_path.is_file():
            raise FileNotFoundError(
                f"Sample.plist not found at {self.sample_plist_path}"
            )

        console.print(
            f"  [dim]Loading template:[/dim] {self.sample_plist_path.name}"
        )
        with open(self.sample_plist_path, "rb") as fp:
            config: Dict[str, Any] = plistlib.load(fp)

        # Apply each section patch in order.
        self._populate_acpi(config)
        self._populate_booter(config)
        self._populate_device_properties(config)
        self._populate_kernel(config)
        self._populate_misc(config)
        self._populate_nvram(config)
        self._populate_platform_info(config)
        self._populate_uefi(config)

        # Strict assertion against WhateverGreen
        if (self.kexts_path / "WhateverGreen.kext").exists():
            raise AssertionError(
                "STRICT RULE VIOLATION: WhateverGreen.kext detected in dist/EFI/OC/Kexts! Aborting."
            )

        # Ensure parent directory exists and write the final plist.
        self.config_plist_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.config_plist_path, "wb") as fp:
            plistlib.dump(config, fp, sort_keys=False)

        console.print(
            f"\n  [bold green]✓[/bold green] config.plist written → "
            f"[underline]{self.config_plist_path}[/underline]"
        )
        return self.config_plist_path

    # ------------------------------------------------------------------
    # ACPI
    # ------------------------------------------------------------------

    def _populate_acpi(self, config: Dict[str, Any]) -> None:
        """Register every ``.aml`` in the ACPI directory and add the
        ``_OSI → XOSI`` rename patch."""
        console.print("\n  [bold]ACPI[/bold]")

        acpi = config.setdefault("ACPI", {})

        # --- ACPI -> Add ---
        aml_entries: List[Dict[str, Any]] = []
        if self.acpi_path.is_dir():
            for aml_file in sorted(self.acpi_path.glob("*.aml")):
                entry = {
                    "Comment": aml_file.stem,
                    "Enabled": True,
                    "Path": aml_file.name,
                }
                aml_entries.append(entry)
                console.print(f"    [green]+[/green] {aml_file.name}")
        else:
            console.print("    [yellow]⚠ ACPI directory not found — skipping Add[/yellow]")

        acpi["Add"] = aml_entries

        # --- ACPI -> Patch (_OSI → XOSI rename) ---
        osi_patch: Dict[str, Any] = {
            "Base": "",
            "BaseSkip": 0,
            "Comment": "_OSI to XOSI rename",
            "Count": 0,
            "Enabled": True,
            "Find": bytes.fromhex("5F4F5349"),
            "Limit": 0,
            "Mask": b"",
            "OemTableId": b"",
            "Replace": bytes.fromhex("584F5349"),
            "ReplaceMask": b"",
            "Skip": 0,
            "TableLength": 0,
            "TableSignature": b"",
        }
        acpi["Patch"] = [osi_patch]
        console.print("    [green]+[/green] _OSI → XOSI rename patch")

    # ------------------------------------------------------------------
    # Booter
    # ------------------------------------------------------------------

    def _populate_booter(self, config: Dict[str, Any]) -> None:
        """Set Tiger Lake booter quirks."""
        console.print("\n  [bold]Booter → Quirks[/bold]")

        booter = config.setdefault("Booter", {})
        quirks = booter.setdefault("Quirks", {})

        tgl_quirks: Dict[str, Any] = {
            "DevirtualiseMmio": True,
            "EnableWriteUnprotector": False,
            "RebuildAppleMemoryMap": True,
            "ResizeAppleGpuBars": -1,
            "SetupVirtualMap": True,
            "SyncRuntimePermissions": True,
        }
        quirks.update(tgl_quirks)

        for key, value in tgl_quirks.items():
            console.print(f"    [green]✓[/green] {key} = {value}")

    # ------------------------------------------------------------------
    # DeviceProperties
    # ------------------------------------------------------------------

    def _populate_device_properties(self, config: Dict[str, Any]) -> None:
        """Inject iGPU DVMT pre-alloc bypass via DeviceProperties."""
        console.print("\n  [bold]DeviceProperties → iGPU[/bold]")

        dev_props = config.setdefault("DeviceProperties", {})
        add_section = dev_props.setdefault("Add", {})

        igpu_path = "PciRoot(0x0)/Pci(0x2,0x0)"
        igpu_props: Dict[str, bytes] = {
            "framebuffer-patch-enable": bytes.fromhex("01000000"),
            "framebuffer-stolenmem": bytes.fromhex("00003001"),
            "framebuffer-fbmem": bytes.fromhex("00009000"),
        }
        add_section[igpu_path] = igpu_props

        for prop, val in igpu_props.items():
            console.print(f"    [green]+[/green] {prop} = {val.hex()}")

    # ------------------------------------------------------------------
    # Kernel
    # ------------------------------------------------------------------

    def _populate_kernel(self, config: Dict[str, Any]) -> None:
        """Build the Kernel section: kext list, CPUID emulate, and quirks."""
        console.print("\n  [bold]Kernel[/bold]")

        kernel = config.setdefault("Kernel", {})

        # --- Kernel -> Add (kexts) ---
        kext_entries = self._scan_kexts()
        kernel["Add"] = kext_entries

        # --- Kernel -> Emulate (Tiger Lake → Ice Lake CPUID) ---
        emulate = kernel.setdefault("Emulate", {})
        emulate["Cpuid1Data"] = bytes.fromhex(
            "E5060700000000000000000000000000"
        )
        emulate["Cpuid1Mask"] = bytes.fromhex(
            "FFFFFFFF000000000000000000000000"
        )
        console.print("    [green]✓[/green] CPUID spoof: Tiger Lake → Ice Lake")

        # --- Kernel -> Quirks ---
        quirks = kernel.setdefault("Quirks", {})
        tgl_quirks: Dict[str, Any] = {
            "AppleXcpmCfgLock": True,
            "DisableIoMapper": True,
            "LapicKernelPanic": False,
            "PanicNoKextDump": True,
            "PowerTimeoutKernelPanic": True,
            "XhciPortLimit": False,
        }
        quirks.update(tgl_quirks)
        for key, value in tgl_quirks.items():
            console.print(f"    [green]✓[/green] Quirk  {key} = {value}")

    def _scan_kexts(self) -> List[Dict[str, Any]]:
        """Scan the Kexts directory and return an ordered list of kext
        entries ready for ``Kernel -> Add``.

        The scan respects the canonical ``_KEXT_ORDER`` list so that
        dependencies are always loaded before their dependents.  Kexts
        present on disk but *not* in the order list are appended at the end
        alphabetically.  Kexts listed in the order but missing from disk
        are silently skipped.

        Returns
        -------
        list[dict]
            Kext entry dictionaries suitable for ``Kernel -> Add``.
        """
        console.print("    [bold]Kext loading order:[/bold]")

        if not self.kexts_path.is_dir():
            console.print(
                "    [yellow]⚠ Kexts directory not found — no kexts registered[/yellow]"
            )
            return []

        entries: List[Dict[str, Any]] = []
        registered_bundles: set[str] = set()

        # --- Phase 1: walk the canonical order ---
        for item in _KEXT_ORDER:
            if isinstance(item, tuple):
                parent_name, child_name = item
                entry = self._make_plugin_entry(parent_name, child_name)
                if entry is not None:
                    entries.append(entry)
                    registered_bundles.add(entry["BundlePath"])
            else:
                entry = self._make_toplevel_entry(item)
                if entry is not None:
                    entries.append(entry)
                    registered_bundles.add(entry["BundlePath"])

                    # Auto-discover PlugIns NOT already in the canonical list.
                    plugins_dir = self.kexts_path / item / "Contents" / "PlugIns"
                    if plugins_dir.is_dir():
                        for plugin in sorted(plugins_dir.iterdir()):
                            if plugin.suffix == ".kext":
                                bp = f"{item}/Contents/PlugIns/{plugin.name}"
                                if bp not in registered_bundles:
                                    # Check if it's in the canonical list as
                                    # a tuple — if so, it will be handled
                                    # later; don't duplicate.
                                    canonical_plugin = any(
                                        isinstance(o, tuple)
                                        and o[0] == item
                                        and o[1] == plugin.name
                                        for o in _KEXT_ORDER
                                    )
                                    if not canonical_plugin:
                                        pe = self._make_plugin_entry(
                                            item, plugin.name
                                        )
                                        if pe is not None:
                                            entries.append(pe)
                                            registered_bundles.add(
                                                pe["BundlePath"]
                                            )

        # --- Phase 2: pick up any remaining top-level kexts not in the list ---
        for kext_dir in sorted(self.kexts_path.iterdir()):
            if kext_dir.suffix == ".kext" and kext_dir.name not in {
                e.split("/")[0] if "/" in e else e
                for e in registered_bundles
            }:
                entry = self._make_toplevel_entry(kext_dir.name)
                if entry is not None:
                    entries.append(entry)
                    registered_bundles.add(entry["BundlePath"])

        # Pretty-print the order table.
        table = Table(show_header=True, header_style="bold", pad_edge=False)
        table.add_column("#", style="dim", width=4)
        table.add_column("BundlePath")
        table.add_column("ExecutablePath", style="dim")
        for idx, ent in enumerate(entries, 1):
            table.add_row(str(idx), ent["BundlePath"], ent["ExecutablePath"])
        console.print(table)

        return entries

    # ------------------------------------------------------------------
    # Kext helpers
    # ------------------------------------------------------------------

    def _make_toplevel_entry(
        self, kext_name: str
    ) -> Optional[Dict[str, Any]]:
        """Build a Kernel -> Add entry for a top-level kext.

        Returns ``None`` if the kext directory does not exist on disk.
        """
        kext_dir = self.kexts_path / kext_name
        if not kext_dir.is_dir():
            console.print(
                f"    [dim]skip {kext_name} (not found)[/dim]"
            )
            return None

        bundle_id, executable = self._get_kext_info(kext_dir)
        exec_path = f"Contents/MacOS/{executable}" if executable else ""

        return {
            "Arch": "x86_64",
            "BundlePath": kext_name,
            "Comment": "",
            "Enabled": True,
            "ExecutablePath": exec_path,
            "MaxKernel": "",
            "MinKernel": "",
            "PlistPath": "Contents/Info.plist",
        }

    def _make_plugin_entry(
        self, parent_name: str, child_name: str
    ) -> Optional[Dict[str, Any]]:
        """Build a Kernel -> Add entry for a kext nested inside another
        kext's ``Contents/PlugIns/`` directory.

        Also searches the top-level Kexts directory in case the plugin is
        shipped standalone (e.g. some VoodooI2C satellites).

        Returns ``None`` when the plugin cannot be located.
        """
        # Primary location: inside the parent kext.
        plugin_dir = (
            self.kexts_path
            / parent_name
            / "Contents"
            / "PlugIns"
            / child_name
        )
        bundle_path = f"{parent_name}/Contents/PlugIns/{child_name}"

        # Fallback: top-level Kexts directory.
        if not plugin_dir.is_dir():
            plugin_dir = self.kexts_path / child_name
            bundle_path = child_name
            if not plugin_dir.is_dir():
                console.print(
                    f"    [dim]skip {parent_name} → {child_name} (not found)[/dim]"
                )
                return None

        bundle_id, executable = self._get_kext_info(plugin_dir)
        exec_path = f"Contents/MacOS/{executable}" if executable else ""

        return {
            "Arch": "x86_64",
            "BundlePath": bundle_path,
            "Comment": "",
            "Enabled": True,
            "ExecutablePath": exec_path,
            "MaxKernel": "",
            "MinKernel": "",
            "PlistPath": "Contents/Info.plist",
        }

    def _get_kext_info(self, kext_path: Path) -> Tuple[str, str]:
        """Read a kext's ``Info.plist`` and return its bundle identifier and
        executable name.

        Parameters
        ----------
        kext_path:
            Path to the ``.kext`` bundle directory.

        Returns
        -------
        tuple[str, str]
            ``(CFBundleIdentifier, CFBundleExecutable)``.  Either value may
            be an empty string if the key is absent (e.g. codeless kexts).
        """
        info_plist = kext_path / "Contents" / "Info.plist"
        if not info_plist.is_file():
            console.print(
                f"    [yellow]⚠ No Info.plist in {kext_path.name}[/yellow]"
            )
            return ("", "")

        try:
            with open(info_plist, "rb") as fp:
                info: Dict[str, Any] = plistlib.load(fp)
        except Exception as exc:
            console.print(
                f"    [red]✗ Failed to parse {info_plist}: {exc}[/red]"
            )
            return ("", "")

        bundle_id: str = info.get("CFBundleIdentifier", "")
        executable: str = info.get("CFBundleExecutable", "")
        return (bundle_id, executable)

    # ------------------------------------------------------------------
    # Misc
    # ------------------------------------------------------------------

    def _populate_misc(self, config: Dict[str, Any]) -> None:
        """Set Misc -> Security options for first-boot convenience."""
        console.print("\n  [bold]Misc → Security[/bold]")

        misc = config.setdefault("Misc", {})
        security = misc.setdefault("Security", {})

        sec_settings: Dict[str, str] = {
            "SecureBootModel": "Disabled",
            "DmgLoading": "Any",
            "Vault": "Optional",
        }
        security.update(sec_settings)

        for key, value in sec_settings.items():
            console.print(f"    [green]✓[/green] {key} = {value}")

    # ------------------------------------------------------------------
    # NVRAM
    # ------------------------------------------------------------------

    def _populate_nvram(self, config: Dict[str, Any]) -> None:
        """Populate NVRAM boot-args and SIP configuration."""
        console.print("\n  [bold]NVRAM[/bold]")

        nvram = config.setdefault("NVRAM", {})
        add_section = nvram.setdefault("Add", {})

        apple_guid = "7C436110-AB2A-4BBB-A880-FE41995C9F82"
        guid_dict = add_section.setdefault(apple_guid, {})

        boot_args = (
            "-v debug=0x100 keepsyms=1 "
            "-allow3d -disablegfxfirmware "
            "-igfxdvmt -vi2c-force-polling alcid=11"
        )
        guid_dict["boot-args"] = boot_args
        guid_dict["csr-active-config"] = bytes.fromhex("03080000")

        console.print(f"    [green]+[/green] boot-args = {boot_args}")
        console.print(
            f"    [green]+[/green] csr-active-config = 03080000"
        )

    # ------------------------------------------------------------------
    # PlatformInfo
    # ------------------------------------------------------------------

    def _populate_platform_info(self, config: Dict[str, Any]) -> None:
        """Inject SMBIOS identity into PlatformInfo -> Generic."""
        console.print("\n  [bold]PlatformInfo → Generic[/bold]")

        plat = config.setdefault("PlatformInfo", {})
        generic = plat.setdefault("Generic", {})

        fields = [
            "SystemProductName",
            "SystemSerialNumber",
            "MLB",
            "SystemUUID",
            "ROM",
        ]
        for field in fields:
            value = self.smbios_data.get(field)
            if value is None:
                console.print(
                    f"    [yellow]⚠ {field} missing from smbios_data[/yellow]"
                )
                continue
            generic[field] = value
            # Mask serial / MLB in console output for privacy.
            if field in ("SystemSerialNumber", "MLB"):
                display = str(value)[:4] + "••••••"
            elif field == "ROM" and isinstance(value, (bytes, bytearray)):
                display = value.hex()
            else:
                display = str(value)
            console.print(f"    [green]✓[/green] {field} = {display}")

    # ------------------------------------------------------------------
    # UEFI
    # ------------------------------------------------------------------

    def _populate_uefi(self, config: Dict[str, Any]) -> None:
        """Register existing drivers in UEFI -> Drivers."""
        console.print("\n  [bold]UEFI → Drivers[/bold]")

        uefi = config.setdefault("UEFI", {})
        drivers_dir = self.efi_oc_path / "Drivers"
        if not drivers_dir.is_dir():
            console.print("    [yellow]⚠ Drivers directory not found[/yellow]")
            return

        # Canonical driver order
        driver_order = ["HfsPlus.efi", "OpenRuntime.efi", "ResetNvramEntry.efi"]
        available_drivers = [p.name for p in drivers_dir.glob("*.efi")]

        # Determine if existing sample plist uses dict or str
        sample_drivers = uefi.get("Drivers", [])
        use_dict = True
        if sample_drivers and isinstance(sample_drivers[0], str):
            use_dict = False

        driver_entries = []
        ordered_list = [d for d in driver_order if d in available_drivers]
        ordered_list.extend(sorted(d for d in available_drivers if d not in driver_order))

        for drv in ordered_list:
            if use_dict:
                driver_entries.append({
                    "Arguments": "",
                    "Comment": drv,
                    "Enabled": True,
                    "HideVerbose": False,
                    "LoadEarly": False,
                    "Path": drv,
                })
            else:
                driver_entries.append(drv)
            console.print(f"    [green]+[/green] Driver: {drv}")

        uefi["Drivers"] = driver_entries
