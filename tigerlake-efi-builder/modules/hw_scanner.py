"""
hw_scanner.py - Hardware Scanner Module for Tiger Lake EFI Builder
=================================================================

Inspects the host machine via WMI and Windows Registry to detect hardware
components relevant to OpenCore Hackintosh configuration. Produces a
structured hardware_report.json for downstream EFI generation.

Targets: 11th Gen Intel Tiger Lake laptops (e.g. with Iris Xe + MX450).
"""

from __future__ import annotations

import json
import os
import re
import sys
import winreg
from pathlib import Path
from typing import Any, Optional

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

try:
    import wmi  # type: ignore[import-untyped]
except Exception:
    wmi = None  # Handled gracefully below

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = PROJECT_ROOT / "hardware_report.json"

# PCI Vendor IDs
VEN_INTEL = "VEN_8086"
VEN_NVIDIA = "VEN_10DE"
VEN_REALTEK = "VEN_10EC"

# Intel Iris Xe Graphics (Tiger Lake GT2) device IDs
IRIS_XE_DEVICE_IDS: list[str] = [
    "DEV_9A49",  # Iris Xe Graphics (TGL-U GT2)
    "DEV_9A40",  # Iris Xe Graphics (TGL-U GT1)
    "DEV_9A60",  # Iris Xe MAX
    "DEV_9A68",
    "DEV_9A70",
    "DEV_9A78",
]

# PS/2 keyboard PnP prefixes
PS2_KEYBOARD_IDS: list[str] = [
    "ACPI\\PNP0303",
    "ACPI\\MSF0001",
    "ACPI\\ATK3001",
    "ACPI\\PNP030B",
    "ACPI\\IBM0057",
]

# I2C trackpad vendor prefixes
I2C_TRACKPAD_PREFIXES: list[str] = [
    "ACPI\\SYNA",
    "ACPI\\ELAN",
    "ACPI\\ALPS",
    "ACPI\\CUST",
    "ACPI\\MSFT",
]

console = Console(legacy_windows=False)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_wmi_connection() -> Optional[Any]:
    """Attempt to create a WMI connection, returning None on failure."""
    if wmi is None:
        console.print(
            "[bold red]✗[/] [yellow]python-wmi package is not installed. "
            "Install with: pip install wmi[/]"
        )
        return None
    try:
        return wmi.WMI()
    except Exception as exc:
        console.print(f"[bold red]✗[/] [yellow]Failed to connect to WMI: {exc}[/]")
        return None


def _extract_pci_ids(pnp_device_id: str) -> str:
    """Extract 'VEN_XXXX&DEV_XXXX' substring from a PnP Device ID."""
    match = re.search(r"(VEN_[0-9A-Fa-f]{4}&DEV_[0-9A-Fa-f]{4})", pnp_device_id)
    return match.group(1).upper() if match else ""


def _extract_device_id(pnp_device_id: str) -> str:
    """Extract just the DEV_XXXX portion from a PnP Device ID."""
    match = re.search(r"DEV_([0-9A-Fa-f]{4})", pnp_device_id)
    return f"0x{match.group(1).lower()}" if match else ""


def _extract_vendor_id(pnp_device_id: str) -> str:
    """Extract just the VEN_XXXX portion from a PnP Device ID."""
    match = re.search(r"VEN_([0-9A-Fa-f]{4})", pnp_device_id)
    return f"0x{match.group(1).lower()}" if match else ""


def _registry_read(key_path: str, value_name: str) -> Optional[str]:
    """Read a string value from the Windows registry under HKLM."""
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
            val, _ = winreg.QueryValueEx(key, value_name)
            return str(val)
    except FileNotFoundError:
        return None
    except OSError:
        return None


def _parse_location_to_acpi_path(location_info: str) -> str:
    """
    Parse a LocationInformation string into an ACPI-style path.

    Typical formats:
      - ``PCI bus 0, device 1, function 0`` → ``\\_SB.PCI0.PEG0.PEGP``
      - ``ACPI(_SB_)#ACPI(PCI0)#ACPI(PEG0)#ACPI(PEGP)`` → ``\\_SB.PCI0.PEG0.PEGP``
      - ``@0,2`` or similar short forms
    """
    if not location_info:
        return ""

    # Direct ACPI path format: ACPI(xxx)#ACPI(yyy)#...
    acpi_parts = re.findall(r"ACPI\(([^)]+)\)", location_info)
    if acpi_parts:
        # Normalise: _SB_ → SB, strip underscores from padding
        cleaned = []
        for part in acpi_parts:
            p = part.strip("_")
            if p == "SB":
                cleaned.append("_SB")
            else:
                cleaned.append(p)
        return "\\_SB." + ".".join(cleaned[1:]) if cleaned and cleaned[0] == "_SB" else ".".join(cleaned)

    # PCI bus/device/function format → generate a conventional ACPI path
    bus_match = re.search(r"bus\s+(\d+)", location_info, re.IGNORECASE)
    dev_match = re.search(r"device\s+(\d+)", location_info, re.IGNORECASE)
    func_match = re.search(r"function\s+(\d+)", location_info, re.IGNORECASE)
    if bus_match and dev_match:
        bus = int(bus_match.group(1))
        dev = int(dev_match.group(1))
        # Common Tiger Lake PEG mappings
        if bus == 0 and dev == 1:
            return "\\_SB.PCI0.PEG0.PEGP"
        if bus == 0 and dev == 2:
            return "\\_SB.PCI0.GFX0"  # iGPU typically
        if bus == 0 and dev == 6:
            return "\\_SB.PCI0.PEG1.PEGP"
        # Generic RP mapping for other root-port devices
        if dev >= 28:
            rp_index = dev - 28
            return f"\\_SB.PCI0.RP{rp_index:02d}.PXSX"
        return f"\\_SB.PCI0.DEV{dev}"

    # Return raw if we can't parse
    return location_info


def _resolve_dgpu_acpi_path(pnp_device_id: str, wmi_conn: Any) -> str:
    """
    Attempt to resolve the ACPI path of a dGPU using multiple strategies:
    1. Registry LocationInformation
    2. WMI GetDeviceProperties (DEVPKEY_Device_LocationPaths)
    3. Fallback heuristic based on PCI bus topology
    """
    acpi_path = ""

    # --- Strategy 1: Registry LocationInformation ---
    # PnP ID format: PCI\VEN_XXXX&DEV_YYYY&SUBSYS_ZZZZ&REV_RR\InstanceId
    parts = pnp_device_id.split("\\")
    if len(parts) >= 3:
        device_key = parts[1]  # VEN_XXXX&DEV_YYYY&SUBSYS_...
        instance_id = parts[2]
        reg_path = f"SYSTEM\\CurrentControlSet\\Enum\\PCI\\{device_key}\\{instance_id}"
        location_info = _registry_read(reg_path, "LocationInformation")
        if location_info:
            acpi_path = _parse_location_to_acpi_path(location_info)
            if acpi_path:
                return acpi_path

    # --- Strategy 2: WMI GetDeviceProperties ---
    if wmi_conn is not None:
        try:
            query = (
                f"SELECT * FROM Win32_PnPEntity WHERE PNPDeviceID = "
                f"'{pnp_device_id.replace(chr(92), chr(92)*2)}'"
            )
            entities = wmi_conn.query(query)
            for entity in entities:
                try:
                    # GetDeviceProperties with DEVPKEY_Device_LocationPaths
                    # {540b947e-8b40-45bc-a8a2-6a0b894cbda2} 37
                    props = entity.GetDeviceProperties(
                        ["{540b947e-8b40-45bc-a8a2-6a0b894cbda2} 37"]
                    )
                    if props and len(props) > 0:
                        for prop in props:
                            prop_data = getattr(prop, "Data", None)
                            if prop_data:
                                data_list = prop_data if isinstance(prop_data, list) else [prop_data]
                                for entry in data_list:
                                    entry_str = str(entry)
                                    if "ACPI" in entry_str.upper():
                                        parsed = _parse_location_to_acpi_path(entry_str)
                                        if parsed:
                                            return parsed
                except Exception:
                    pass  # GetDeviceProperties may not be available
        except Exception:
            pass

    # --- Strategy 3: Enumerate via registry to find BIOS device name ---
    if len(parts) >= 3:
        device_key = parts[1]
        instance_id = parts[2]
        reg_path = f"SYSTEM\\CurrentControlSet\\Enum\\PCI\\{device_key}\\{instance_id}"
        # Try the "BIOS Device Name" value that some systems expose
        bios_name = _registry_read(reg_path, "BIOS Device Name")
        if bios_name:
            return bios_name

    # --- Fallback: common Tiger Lake dGPU ACPI path ---
    if not acpi_path:
        acpi_path = "\\_SB.PCI0.PEG0.PEGP"  # Most common for TGL dGPU

    return acpi_path


# ---------------------------------------------------------------------------
# Detection Functions
# ---------------------------------------------------------------------------


def _detect_cpu(wmi_conn: Any) -> dict[str, Any]:
    """Detect CPU information via WMI Win32_Processor."""
    cpu_info: dict[str, Any] = {
        "name": "Unknown",
        "cores": 0,
        "threads": 0,
        "is_tiger_lake": False,
    }

    if wmi_conn is None:
        return cpu_info

    try:
        processors = wmi_conn.Win32_Processor()
        if processors:
            proc = processors[0]
            cpu_info["name"] = getattr(proc, "Name", "Unknown").strip()
            cpu_info["cores"] = int(getattr(proc, "NumberOfCores", 0))
            cpu_info["threads"] = int(getattr(proc, "NumberOfLogicalProcessors", 0))

            # Tiger Lake detection
            name_lower = cpu_info["name"].lower()
            family = int(getattr(proc, "Family", 0))
            cpuid = str(getattr(proc, "ProcessorId", ""))

            is_11th_gen = "11th gen" in name_lower
            # CPUID family 6 with Tiger Lake model numbers (0x8C, 0x8D)
            is_tgl_cpuid = family == 198 or family == 6  # Intel Core family
            is_tiger_lake_name = any(
                tag in name_lower
                for tag in ["tiger lake", "1135g7", "1145g7", "1155g7", "1165g7", "1185g7"]
            )

            cpu_info["is_tiger_lake"] = is_11th_gen or is_tiger_lake_name
    except Exception as exc:
        console.print(f"[bold red]✗[/] [yellow]CPU detection error: {exc}[/]")

    return cpu_info


def _detect_igpu(wmi_conn: Any) -> dict[str, Any]:
    """Detect Intel Iris Xe iGPU."""
    igpu_info: dict[str, Any] = {
        "name": "Unknown",
        "pci_id": "",
        "device_id": "",
        "verified": False,
    }

    if wmi_conn is None:
        return igpu_info

    try:
        # First try Win32_VideoController
        video_controllers = wmi_conn.Win32_VideoController()
        for vc in video_controllers:
            pnp_id = getattr(vc, "PNPDeviceID", "") or ""
            if VEN_INTEL in pnp_id.upper():
                igpu_info["name"] = getattr(vc, "Name", "Unknown").strip()
                igpu_info["pci_id"] = _extract_pci_ids(pnp_id)
                igpu_info["device_id"] = _extract_device_id(pnp_id)
                # Check if this is an Iris Xe device
                for dev_id in IRIS_XE_DEVICE_IDS:
                    if dev_id in pnp_id.upper():
                        igpu_info["verified"] = True
                        break
                break

        # If not found via VideoController, try PnP entities
        if not igpu_info["verified"]:
            for dev_id in IRIS_XE_DEVICE_IDS:
                query = (
                    f"SELECT * FROM Win32_PnPEntity WHERE "
                    f"PNPDeviceID LIKE '%{VEN_INTEL}%{dev_id}%'"
                )
                entities = wmi_conn.query(query)
                if entities:
                    entity = entities[0]
                    pnp_id = getattr(entity, "PNPDeviceID", "") or ""
                    igpu_info["name"] = getattr(entity, "Name", igpu_info["name"]).strip()
                    igpu_info["pci_id"] = _extract_pci_ids(pnp_id)
                    igpu_info["device_id"] = _extract_device_id(pnp_id)
                    igpu_info["verified"] = True
                    break

    except Exception as exc:
        console.print(f"[bold red]✗[/] [yellow]iGPU detection error: {exc}[/]")

    return igpu_info


def _detect_dgpu(wmi_conn: Any) -> dict[str, Any]:
    """Detect discrete Nvidia GPU (e.g. MX450) and resolve its ACPI path."""
    dgpu_info: dict[str, Any] = {
        "name": "Not Found",
        "pci_id": "",
        "acpi_path": "",
        "needs_disable": False,
    }

    if wmi_conn is None:
        return dgpu_info

    try:
        # Search video controllers for Nvidia
        video_controllers = wmi_conn.Win32_VideoController()
        nvidia_pnp_id = ""
        for vc in video_controllers:
            pnp_id = getattr(vc, "PNPDeviceID", "") or ""
            if VEN_NVIDIA in pnp_id.upper():
                dgpu_info["name"] = getattr(vc, "Name", "Unknown").strip()
                dgpu_info["pci_id"] = _extract_pci_ids(pnp_id)
                dgpu_info["needs_disable"] = True  # macOS cannot use Nvidia Optimus
                nvidia_pnp_id = pnp_id
                break

        # Also search PnP entities if not found via VideoController
        if not nvidia_pnp_id:
            query = (
                f"SELECT * FROM Win32_PnPEntity WHERE "
                f"PNPDeviceID LIKE '%{VEN_NVIDIA}%' AND "
                f"PNPDeviceID LIKE '%PCI%'"
            )
            entities = wmi_conn.query(query)
            for entity in entities:
                pnp_id = getattr(entity, "PNPDeviceID", "") or ""
                name = getattr(entity, "Name", "") or ""
                # Filter for display/3D class devices
                cls = getattr(entity, "PNPClass", "") or ""
                if "display" in cls.lower() or "3d" in name.lower() or "geforce" in name.lower() or "mx" in name.lower():
                    dgpu_info["name"] = name.strip()
                    dgpu_info["pci_id"] = _extract_pci_ids(pnp_id)
                    dgpu_info["needs_disable"] = True
                    nvidia_pnp_id = pnp_id
                    break

        # Resolve ACPI path for the dGPU
        if nvidia_pnp_id:
            dgpu_info["acpi_path"] = _resolve_dgpu_acpi_path(nvidia_pnp_id, wmi_conn)

    except Exception as exc:
        console.print(f"[bold red]✗[/] [yellow]dGPU detection error: {exc}[/]")

    return dgpu_info


def _detect_input_devices(wmi_conn: Any) -> dict[str, Any]:
    """Detect PS/2 keyboard and I2C HID trackpad."""
    input_info: dict[str, Any] = {
        "keyboard": {"type": "Unknown", "pnp_id": ""},
        "trackpad": {"type": "Unknown", "pnp_id": "", "is_i2c": False},
    }

    if wmi_conn is None:
        return input_info

    try:
        # --- Keyboard detection ---
        keyboard_entities = wmi_conn.query(
            "SELECT * FROM Win32_Keyboard"
        )
        for kb in keyboard_entities:
            pnp_id = (getattr(kb, "PNPDeviceID", "") or "").upper()
            for ps2_id in PS2_KEYBOARD_IDS:
                if ps2_id.upper() in pnp_id:
                    input_info["keyboard"] = {
                        "type": "PS/2",
                        "pnp_id": getattr(kb, "PNPDeviceID", ""),
                    }
                    break
            if input_info["keyboard"]["type"] == "PS/2":
                break

        # Fallback: search PnP entities for PS/2 keyboard
        if input_info["keyboard"]["type"] == "Unknown":
            for ps2_id in PS2_KEYBOARD_IDS:
                entities = wmi_conn.query(
                    f"SELECT * FROM Win32_PnPEntity WHERE PNPDeviceID LIKE '%{ps2_id}%'"
                )
                if entities:
                    input_info["keyboard"] = {
                        "type": "PS/2",
                        "pnp_id": getattr(entities[0], "PNPDeviceID", ""),
                    }
                    break

        # --- Trackpad detection ---
        for prefix in I2C_TRACKPAD_PREFIXES:
            entities = wmi_conn.query(
                f"SELECT * FROM Win32_PnPEntity WHERE PNPDeviceID LIKE '%{prefix}%'"
            )
            if entities:
                entity = entities[0]
                pnp_id = getattr(entity, "PNPDeviceID", "") or ""
                name = getattr(entity, "Name", "") or ""
                is_i2c = (
                    "I2C" in name.upper()
                    or "I2C" in pnp_id.upper()
                    or "HID" in pnp_id.upper()
                    or any(p in pnp_id.upper() for p in ["SYNA", "ELAN"])
                )
                input_info["trackpad"] = {
                    "type": "I2C HID" if is_i2c else "PS/2",
                    "pnp_id": pnp_id,
                    "is_i2c": is_i2c,
                }
                break

        # Also try HID\ACPI pattern for I2C trackpads
        if input_info["trackpad"]["type"] == "Unknown":
            entities = wmi_conn.query(
                "SELECT * FROM Win32_PnPEntity WHERE "
                "PNPDeviceID LIKE '%HID\\\\ACPI%' AND "
                "Name LIKE '%touch%pad%'"
            )
            if not entities:
                entities = wmi_conn.query(
                    "SELECT * FROM Win32_PnPEntity WHERE "
                    "PNPDeviceID LIKE '%HID\\\\ACPI%' AND "
                    "Name LIKE '%track%pad%'"
                )
            if entities:
                entity = entities[0]
                input_info["trackpad"] = {
                    "type": "I2C HID",
                    "pnp_id": getattr(entity, "PNPDeviceID", ""),
                    "is_i2c": True,
                }

    except Exception as exc:
        console.print(f"[bold red]✗[/] [yellow]Input device detection error: {exc}[/]")

    return input_info


def _detect_network(wmi_conn: Any) -> dict[str, Any]:
    """Detect Intel Wi-Fi adapter and Bluetooth controller."""
    network_info: dict[str, Any] = {
        "wifi": {"name": "Unknown", "pci_id": "", "is_intel": False},
        "bluetooth": {"name": "Unknown", "usb_id": ""},
    }

    if wmi_conn is None:
        return network_info

    try:
        # --- Wi-Fi adapter ---
        adapters = wmi_conn.Win32_NetworkAdapter()
        for adapter in adapters:
            pnp_id = (getattr(adapter, "PNPDeviceID", "") or "").upper()
            name = getattr(adapter, "Name", "") or ""
            # Look for Intel wireless on PCI bus
            if VEN_INTEL in pnp_id and "PCI" in pnp_id:
                name_lower = name.lower()
                if any(kw in name_lower for kw in ["wireless", "wifi", "wi-fi", "ax201", "ax200", "ax210", "ac 9"]):
                    network_info["wifi"] = {
                        "name": name.strip(),
                        "pci_id": _extract_pci_ids(pnp_id),
                        "is_intel": True,
                    }
                    break

        # Fallback: PnP entities for Intel wireless
        if not network_info["wifi"]["is_intel"]:
            entities = wmi_conn.query(
                f"SELECT * FROM Win32_PnPEntity WHERE "
                f"PNPDeviceID LIKE '%PCI%{VEN_INTEL}%' AND "
                f"Name LIKE '%Wireless%'"
            )
            if not entities:
                entities = wmi_conn.query(
                    f"SELECT * FROM Win32_PnPEntity WHERE "
                    f"PNPDeviceID LIKE '%PCI%{VEN_INTEL}%' AND "
                    f"Name LIKE '%Wi-Fi%'"
                )
            if entities:
                entity = entities[0]
                pnp_id = getattr(entity, "PNPDeviceID", "")
                network_info["wifi"] = {
                    "name": getattr(entity, "Name", "Unknown").strip(),
                    "pci_id": _extract_pci_ids(pnp_id),
                    "is_intel": True,
                }

        # --- Bluetooth ---
        bt_entities = wmi_conn.query(
            "SELECT * FROM Win32_PnPEntity WHERE "
            "PNPDeviceID LIKE '%USB%' AND "
            "Name LIKE '%Bluetooth%'"
        )
        if not bt_entities:
            bt_entities = wmi_conn.query(
                "SELECT * FROM Win32_PnPEntity WHERE "
                "Name LIKE '%Bluetooth%'"
            )
        for entity in bt_entities:
            pnp_id = (getattr(entity, "PNPDeviceID", "") or "").upper()
            name = getattr(entity, "Name", "") or ""
            if "USB" in pnp_id or "BTHUSB" in pnp_id or VEN_INTEL in pnp_id:
                # Extract USB VID/PID if present
                usb_match = re.search(r"VID_([0-9A-Fa-f]{4})&PID_([0-9A-Fa-f]{4})", pnp_id)
                usb_id = f"0x{usb_match.group(1).lower()}:0x{usb_match.group(2).lower()}" if usb_match else pnp_id
                network_info["bluetooth"] = {
                    "name": name.strip(),
                    "usb_id": usb_id,
                }
                break

    except Exception as exc:
        console.print(f"[bold red]✗[/] [yellow]Network detection error: {exc}[/]")

    return network_info


def _detect_audio(wmi_conn: Any) -> dict[str, Any]:
    """Detect Realtek audio codec."""
    audio_info: dict[str, Any] = {
        "name": "Unknown",
        "codec_id": "",
        "alc_layout_id": 0,
    }

    if wmi_conn is None:
        return audio_info

    try:
        # Try Win32_SoundDevice first
        sound_devices = wmi_conn.Win32_SoundDevice()
        for sd in sound_devices:
            pnp_id = (getattr(sd, "PNPDeviceID", "") or "").upper()
            name = getattr(sd, "Name", "") or ""
            if VEN_REALTEK in pnp_id or "REALTEK" in name.upper():
                audio_info["name"] = name.strip()
                audio_info["codec_id"] = _extract_pci_ids(pnp_id)
                break

        # Fallback to PnP entities
        if audio_info["name"] == "Unknown":
            entities = wmi_conn.query(
                f"SELECT * FROM Win32_PnPEntity WHERE "
                f"PNPDeviceID LIKE '%{VEN_REALTEK}%'"
            )
            for entity in entities:
                name = getattr(entity, "Name", "") or ""
                pnp_id = getattr(entity, "PNPDeviceID", "") or ""
                if "audio" in name.lower() or "sound" in name.lower() or "codec" in name.lower():
                    audio_info["name"] = name.strip()
                    audio_info["codec_id"] = _extract_pci_ids(pnp_id)
                    break

        # Also check HDAUDIO bus for codec detection
        if audio_info["name"] == "Unknown":
            entities = wmi_conn.query(
                "SELECT * FROM Win32_PnPEntity WHERE "
                "PNPDeviceID LIKE '%HDAUDIO%' AND "
                "Name LIKE '%Realtek%'"
            )
            if entities:
                entity = entities[0]
                audio_info["name"] = getattr(entity, "Name", "Unknown").strip()
                audio_info["codec_id"] = getattr(entity, "PNPDeviceID", "")

        # Attempt to guess ALC layout-id from the codec name
        if audio_info["name"] != "Unknown":
            audio_info["alc_layout_id"] = _guess_alc_layout_id(audio_info["name"])

    except Exception as exc:
        console.print(f"[bold red]✗[/] [yellow]Audio detection error: {exc}[/]")

    return audio_info


def _guess_alc_layout_id(codec_name: str) -> int:
    """
    Guess an AppleALC layout-id based on the Realtek codec model number.

    This is a best-effort heuristic; the user should verify from the
    AppleALC supported codecs list.
    """
    name_upper = codec_name.upper()

    # Common Realtek codec → layout-id mappings for Tiger Lake laptops
    layout_map: dict[str, int] = {
        "ALC235": 11,
        "ALC255": 31,
        "ALC256": 69,
        "ALC257": 11,
        "ALC269": 44,
        "ALC271": 31,
        "ALC274": 28,
        "ALC275": 28,
        "ALC285": 66,
        "ALC289": 93,
        "ALC293": 28,
        "ALC294": 11,
        "ALC295": 77,
        "ALC298": 72,
        "ALC299": 21,
        "ALC700": 11,
    }

    for model, layout in layout_map.items():
        if model in name_upper:
            return layout

    # Default layout for unknown Realtek codecs
    return 1


def _detect_storage(wmi_conn: Any) -> dict[str, Any]:
    """Detect NVMe storage controllers."""
    storage_info: dict[str, Any] = {
        "nvme_detected": False,
        "controller": "Unknown",
    }

    if wmi_conn is None:
        return storage_info

    try:
        # Check Win32_DiskDrive for NVMe
        disk_drives = wmi_conn.Win32_DiskDrive()
        for dd in disk_drives:
            interface = (getattr(dd, "InterfaceType", "") or "").upper()
            model = getattr(dd, "Model", "") or ""
            pnp_id = (getattr(dd, "PNPDeviceID", "") or "").upper()
            if "NVME" in interface or "NVME" in model.upper() or "NVME" in pnp_id:
                storage_info["nvme_detected"] = True
                storage_info["controller"] = model.strip()
                break

        # Also check PnP entities for NVMe controllers
        if not storage_info["nvme_detected"]:
            entities = wmi_conn.query(
                "SELECT * FROM Win32_PnPEntity WHERE "
                "Name LIKE '%NVMe%' OR Name LIKE '%Non-Volatile%'"
            )
            if entities:
                storage_info["nvme_detected"] = True
                storage_info["controller"] = getattr(entities[0], "Name", "NVMe Controller").strip()

        # Fallback: check SCSI controllers
        if not storage_info["nvme_detected"]:
            entities = wmi_conn.query(
                "SELECT * FROM Win32_PnPEntity WHERE "
                "PNPDeviceID LIKE '%SCSI%NVME%'"
            )
            if entities:
                storage_info["nvme_detected"] = True
                storage_info["controller"] = getattr(entities[0], "Name", "NVMe Controller").strip()

    except Exception as exc:
        console.print(f"[bold red]✗[/] [yellow]Storage detection error: {exc}[/]")

    return storage_info


def _detect_machine(wmi_conn: Any) -> dict[str, Any]:
    """Detect machine manufacturer, model, and BIOS version."""
    machine_info: dict[str, Any] = {
        "manufacturer": "Unknown",
        "model": "Unknown",
        "bios_version": "Unknown",
    }

    if wmi_conn is None:
        return machine_info

    try:
        systems = wmi_conn.Win32_ComputerSystem()
        if systems:
            sys_info = systems[0]
            machine_info["manufacturer"] = getattr(sys_info, "Manufacturer", "Unknown").strip()
            machine_info["model"] = getattr(sys_info, "Model", "Unknown").strip()

        bios_entries = wmi_conn.Win32_BIOS()
        if bios_entries:
            bios = bios_entries[0]
            version = getattr(bios, "SMBIOSBIOSVersion", "") or getattr(bios, "Version", "Unknown")
            machine_info["bios_version"] = str(version).strip()

    except Exception as exc:
        console.print(f"[bold red]✗[/] [yellow]Machine info detection error: {exc}[/]")

    return machine_info


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def scan() -> dict[str, Any]:
    """
    Perform a full hardware scan of the host machine.

    Returns:
        A dictionary containing the complete hardware report with keys:
        ``cpu``, ``igpu``, ``dgpu``, ``input``, ``network``, ``audio``,
        ``storage``, ``machine``.

    The report is also saved to ``hardware_report.json`` in the project root.
    """
    console.print()
    console.print(
        Panel(
            "[bold cyan]Tiger Lake Hardware Scanner[/]\n"
            "[dim]Scanning host machine for OpenCore EFI configuration...[/]",
            box=box.DOUBLE,
            border_style="cyan",
        )
    )
    console.print()

    wmi_conn = _safe_wmi_connection()

    # Run all detections
    with console.status("[bold green]Scanning CPU...[/]"):
        cpu = _detect_cpu(wmi_conn)
    _print_detection_result("CPU", cpu["name"], cpu["is_tiger_lake"])

    with console.status("[bold green]Scanning iGPU...[/]"):
        igpu = _detect_igpu(wmi_conn)
    _print_detection_result("iGPU", igpu["name"], igpu["verified"])

    with console.status("[bold green]Scanning dGPU...[/]"):
        dgpu = _detect_dgpu(wmi_conn)
    _print_detection_result(
        "dGPU",
        dgpu["name"],
        dgpu["needs_disable"],
        extra=f"ACPI: {dgpu['acpi_path']}" if dgpu["acpi_path"] else None,
    )

    with console.status("[bold green]Scanning input devices...[/]"):
        input_devs = _detect_input_devices(wmi_conn)
    _print_detection_result("Keyboard", f"{input_devs['keyboard']['type']} — {input_devs['keyboard']['pnp_id']}")
    _print_detection_result(
        "Trackpad",
        f"{input_devs['trackpad']['type']} — {input_devs['trackpad']['pnp_id']}",
        input_devs["trackpad"]["is_i2c"],
    )

    with console.status("[bold green]Scanning network...[/]"):
        network = _detect_network(wmi_conn)
    _print_detection_result("Wi-Fi", network["wifi"]["name"], network["wifi"]["is_intel"])
    _print_detection_result("Bluetooth", network["bluetooth"]["name"])

    with console.status("[bold green]Scanning audio...[/]"):
        audio = _detect_audio(wmi_conn)
    _print_detection_result(
        "Audio",
        audio["name"],
        extra=f"layout-id: {audio['alc_layout_id']}" if audio["alc_layout_id"] else None,
    )

    with console.status("[bold green]Scanning storage...[/]"):
        storage = _detect_storage(wmi_conn)
    _print_detection_result("NVMe", storage["controller"], storage["nvme_detected"])

    with console.status("[bold green]Reading machine info...[/]"):
        machine = _detect_machine(wmi_conn)
    _print_detection_result("Machine", f"{machine['manufacturer']} {machine['model']}")

    # Assemble report
    report: dict[str, Any] = {
        "cpu": cpu,
        "igpu": igpu,
        "dgpu": dgpu,
        "input": input_devs,
        "network": network,
        "audio": audio,
        "storage": storage,
        "machine": machine,
    }

    # Save to disk
    try:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(REPORT_PATH, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, ensure_ascii=False)
        console.print()
        console.print(
            f"[bold green]✓[/] Hardware report saved to [cyan]{REPORT_PATH}[/]"
        )
    except OSError as exc:
        console.print(f"[bold red]✗[/] [yellow]Failed to save report: {exc}[/]")

    console.print()
    return report


def _print_detection_result(
    label: str,
    value: str,
    status: Optional[bool] = None,
    extra: Optional[str] = None,
) -> None:
    """Print a single detection result line with icon and optional status."""
    if status is True:
        icon = "[bold green]✓[/]"
    elif status is False:
        icon = "[bold yellow]⚠[/]"
    else:
        icon = "[bold blue]•[/]"

    line = f"  {icon} [bold white]{label:12s}[/] │ {value}"
    if extra:
        line += f"  [dim]({extra})[/]"
    console.print(line)


def display_report(report: dict[str, Any]) -> None:
    """
    Display a previously generated hardware report with rich formatting.

    Args:
        report: The hardware report dictionary (as returned by :func:`scan`).
    """
    console.print()
    console.print(
        Panel(
            "[bold cyan]Hardware Report Summary[/]",
            box=box.DOUBLE,
            border_style="cyan",
        )
    )

    # --- Machine ---
    machine = report.get("machine", {})
    console.print(
        f"\n  [bold]Machine:[/] {machine.get('manufacturer', '?')} "
        f"{machine.get('model', '?')}  "
        f"[dim](BIOS {machine.get('bios_version', '?')})[/]"
    )

    # --- Main hardware table ---
    table = Table(
        box=box.ROUNDED,
        show_header=True,
        header_style="bold magenta",
        title="Detected Components",
        title_style="bold white",
        padding=(0, 1),
    )
    table.add_column("Component", style="bold cyan", min_width=12)
    table.add_column("Name / Value", min_width=30)
    table.add_column("ID", style="dim")
    table.add_column("Status", justify="center")

    # CPU
    cpu = report.get("cpu", {})
    table.add_row(
        "CPU",
        cpu.get("name", "?"),
        f"{cpu.get('cores', '?')}C / {cpu.get('threads', '?')}T",
        _status_badge(cpu.get("is_tiger_lake", False), "Tiger Lake", "Not TGL"),
    )

    # iGPU
    igpu = report.get("igpu", {})
    table.add_row(
        "iGPU",
        igpu.get("name", "?"),
        igpu.get("device_id", ""),
        _status_badge(igpu.get("verified", False), "Verified", "Unverified"),
    )

    # dGPU
    dgpu = report.get("dgpu", {})
    dgpu_status = "[red]Disable[/]" if dgpu.get("needs_disable") else "[green]OK[/]"
    table.add_row(
        "dGPU",
        dgpu.get("name", "?"),
        dgpu.get("acpi_path", ""),
        dgpu_status,
    )

    # Keyboard
    kb = report.get("input", {}).get("keyboard", {})
    table.add_row("Keyboard", kb.get("type", "?"), kb.get("pnp_id", ""), "")

    # Trackpad
    tp = report.get("input", {}).get("trackpad", {})
    table.add_row(
        "Trackpad",
        tp.get("type", "?"),
        tp.get("pnp_id", ""),
        _status_badge(tp.get("is_i2c", False), "I2C", "Not I2C"),
    )

    # Wi-Fi
    wifi = report.get("network", {}).get("wifi", {})
    table.add_row(
        "Wi-Fi",
        wifi.get("name", "?"),
        wifi.get("pci_id", ""),
        _status_badge(wifi.get("is_intel", False), "Intel", "Other"),
    )

    # Bluetooth
    bt = report.get("network", {}).get("bluetooth", {})
    table.add_row("Bluetooth", bt.get("name", "?"), bt.get("usb_id", ""), "")

    # Audio
    audio = report.get("audio", {})
    layout_id = audio.get("alc_layout_id", 0)
    table.add_row(
        "Audio",
        audio.get("name", "?"),
        audio.get("codec_id", ""),
        f"layout-id: [bold]{layout_id}[/]" if layout_id else "[yellow]?[/]",
    )

    # Storage
    storage = report.get("storage", {})
    table.add_row(
        "Storage",
        storage.get("controller", "?"),
        "",
        _status_badge(storage.get("nvme_detected", False), "NVMe", "No NVMe"),
    )

    console.print()
    console.print(table)

    # --- Recommendations ---
    console.print()
    recs: list[str] = []

    if not cpu.get("is_tiger_lake", False):
        recs.append("[yellow]⚠  CPU may not be Tiger Lake — verify before proceeding.[/]")
    if not igpu.get("verified", False):
        recs.append("[yellow]⚠  iGPU not verified as Iris Xe — device-id spoofing may be needed.[/]")
    if dgpu.get("needs_disable", False):
        recs.append(
            f"[red]✗  dGPU ({dgpu.get('name', '?')}) must be disabled via SSDT-dGPU-Off "
            f"at [bold]{dgpu.get('acpi_path', '?')}[/][/]"
        )
    if not wifi.get("is_intel", False):
        recs.append("[yellow]⚠  Non-Intel Wi-Fi detected — AirportItlwm may not work.[/]")
    if storage.get("nvme_detected", False):
        recs.append("[blue]ℹ  NVMe detected — NVMeFix.kext recommended.[/]")
    if tp.get("is_i2c", False):
        recs.append("[blue]ℹ  I2C trackpad detected — VoodooI2C + VoodooI2CHID kexts needed.[/]")
    if layout_id:
        recs.append(f"[blue]ℹ  Suggested AppleALC layout-id: {layout_id} (verify on AppleALC wiki).[/]")

    if recs:
        console.print(
            Panel(
                "\n".join(recs),
                title="[bold]Recommendations[/]",
                border_style="yellow",
                box=box.ROUNDED,
            )
        )
    else:
        console.print("[bold green]✓ All hardware looks compatible![/]")

    console.print()


def _status_badge(ok: bool, ok_text: str, fail_text: str) -> str:
    """Return a rich-formatted status badge."""
    if ok:
        return f"[bold green]{ok_text}[/]"
    return f"[bold yellow]{fail_text}[/]"


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    console.print("[bold]Running standalone hardware scan...[/]\n")
    hw_report = scan()
    display_report(hw_report)
    console.print(f"[dim]Report written to: {REPORT_PATH}[/]")
