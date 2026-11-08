# Lenovo IdeaPad Slim 5 14ITL05 — macOS Tahoe 26 Hackintosh EFI
### Intel Core i5-1135G7 (Tiger Lake) with Full Metal 3 Hardware Acceleration & Native Audio

![macOS Tahoe](https://img.shields.io/badge/macOS-Tahoe%2026-blue.svg)
![Darwin](https://img.shields.io/badge/Darwin-25.6.0-black.svg)
![OpenCore](https://img.shields.io/badge/OpenCore-1.0.x-green.svg)
![Metal](https://img.shields.io/badge/Metal-Metal%203%20Active-brightgreen.svg)
![Status](https://img.shields.io/badge/Status-100%25%20Working-success.svg)

---

## Overview

This repository contains the complete OpenCore EFI and exhaustive reverse-engineering documentation for running **macOS Tahoe 26 (Darwin 25.6.0)** on the **Lenovo IdeaPad Slim 5 14ITL05** laptop.

While Intel 11th Gen Tiger Lake processors are officially unsupported by Apple, this EFI achieves **full Metal 3 hardware acceleration** on the Intel Iris Xe Graphics (80 EUs) via a custom 6-byte reverse-engineered binary patch to WhateverXe, as well as full native audio restoration via `SimpleHDA`.

---

## Hardware Specifications

| Component | Target Specification |
| :--- | :--- |
| **Model** | Lenovo IdeaPad Slim 5 14ITL05 |
| **Processor** | Intel Core i5-1135G7 (4 Cores / 8 Threads, up to 4.2 GHz) |
| **Graphics** | Intel Iris Xe Graphics (80 Execution Units, PCI ID `8086:9A49`) |
| **Memory** | 8 GB DDR4 3200 MHz Dual-Channel |
| **Internal Display** | 14.0" Full HD (1920x1080) IPS eDP |
| **Audio** | Realtek ALC257 / ALC287 on Intel cAVS (`8086:A0C8`) |
| **Storage** | 128 GB NVMe SSD (macOS) + 1.0 TB HDD (Data) |
| **Wireless / BT** | Intel Wi-Fi 6 AX201 (CNVi) + Bluetooth 5.2 |
| **Trackpad** | I2C HID Digitizer (`MSFT0001`, GPIO Pin `0x006E`) |
| **Keyboard** | Internal PS/2 Keyboard with Brightness Fn Keys |
| **Battery** | Integrated 56.5Wh with Lenovo Conservation Mode |

---

## Working Status Matrix

| Subsystem | Status | Notes |
| :--- | :---: | :--- |
| **Intel Iris Xe Graphics** | **WORKING** | Full Metal 3 acceleration, 1536 MB VRAM, 60 FPS WindowServer compositing |
| **Display Backlight** | **WORKING** | Smooth PWM brightness slider via `SSDT-PNLF` |
| **Internal Audio Speakers** | **WORKING** | Restored via `SimpleHDA.kext` + AppleALC layout `100` |
| **3.5mm Headphone Jack** | **WORKING** | Auto-sensing and volume slider synchronization via `SimpleHDA-HAL` |
| **Internal Microphone** | **WORKING** | Crystal clear input |
| **Wi-Fi** | **WORKING** | 802.11ac/ax 5GHz via `AirportItlwm.kext` (macOS Tahoe build) |
| **Bluetooth** | **WORKING** | Intel AX201 Bluetooth with auto-reconnect via `IntelBTPatcher` + `BlueToolFixup` |
| **Trackpad Gestures** | **WORKING** | Native AppleMultitouch pinch-zoom, 3-finger swipe via `VoodooI2C` (GPIO pinned) |
| **Internal Keyboard** | **WORKING** | Full PS/2 keyboard with remapped Command key and brightness hotkeys |
| **Battery Telemetry** | **WORKING** | Battery percentage, charging rate, and Lenovo 60% Conservation Mode |
| **Power Management** | **WORKING** | X86PlatformPlugin frequency scaling and dynamic sleep/wake states |
| **USB Ports** | **WORKING** | Custom `UTBMap.kext` with exact port personalities (Type-A, Type-C PD) |

---

## The Breakthrough: Reverse-Engineering Iris Xe 80 EU Metal Acceleration

For a comprehensive technical deep-dive into the disassembly and mathematical derivation of the 6-byte patch, please read:
👉 **[DEBUG_REPORT.md](DEBUG_REPORT.md)**

### Quick Summary of the Fix:
- **The Issue:** `WhateverXe` had hardcoded a check for **8 sampler slices** (`cmp [rbp - 0x20], 8`) in `XELP::decodeTGLGT2` (offset `0x1e830`).
- **The Math:** Core i7-1165G7 (96 EU) has 8 slices. However, Core i5-1135G7 (80 EU) has **6 slices** ($8 - 2 = 6$).
- **The Consequence:** On 80 EU chips, $6 
eq 8$ evaluated to `False`, writing `TGLGT2Qualified = No` and preventing the `AppleIntelTGLGraphics` accelerator from ever attaching. WindowServer ran on laggy software rasterization and all Metal apps crashed.
- **The Patch:** Changed `cmp [rbp - 0x20], 8; je ...` to `cmp [rbp - 0x20], 6; jae ...`:
  - Find: `83 7d e0 08 74 06`
  - Replace: `83 7d e0 06 73 06`
- **Result:** Qualifies all Tiger Lake GT2 chips with $\ge 6$ slices, bringing Metal 3 instantly to life!

---

## macOS Tahoe Audio Stack Restoration

In **macOS Tahoe 26**, Apple completely removed `AppleHDA.kext` and `AppleHDAController.kext`.
- **Solution:** Inject `SimpleHDA.kext` (by laobamac) via OpenCore in sequence:
  `Lilu.kext` $
ightarrow$ `AppleALC.kext` $
ightarrow$ `SimpleHDA.kext`.
- **DeviceProperties:** Injected Ice Lake controller spoof `0x34C8` at `PciRoot(0x0)/Pci(0x1f,0x3)` with layout `100` (`alcid=100`).
- **HAL Plugin:** Installed `SimpleHDA-HAL-1.0.1.pkg` to restore native volume sliders.

---

## Installation & Deployment

1. Format your USB drive as **GUID Partition Scheme** with a **FAT32** EFI partition.
2. Clone this repository and copy the `EFI` folder to the root of your EFI partition:
   ```bash
   cp -R EFI /Volumes/EFI/
   ```
3. Boot from the USB drive and select your macOS Tahoe installer or system drive.
4. Once booted into macOS, install the HAL audio package if not already installed:
   ```bash
   sudo installer -pkg SimpleHDA-HAL-1.0.1.pkg -target /
   ```
5. Clone the EFI to your internal SSD EFI partition (`disk0s1`) for standalone booting.

---

## Author & Credits

- Developed and reverse-engineered by **donvipura**.
- OpenCore Team (`Acidanthera`) for Lilu, VirtualSMC, and AppleALC.
- `laobamac` for SimpleHDA.
- `b00t0x` and the Hackintosh community for WhateverXe foundations.
