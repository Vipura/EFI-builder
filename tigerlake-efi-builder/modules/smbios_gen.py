"""
smbios_gen.py – Randomised, valid MacBookAir9,1 SMBIOS value generator.

Generates plausible Apple-format serial numbers, MLB board serials,
SystemUUID, and ROM values suitable for OpenCore config.plist injection.

Apple 12-character serial format (post-2010):
  [Factory 3-4][Year 1][Week 1][Unit 3][Model 4]

No vowels (A E I O U) are used in the random segments – this matches
Apple's own convention and reduces the chance of generating offensive
strings.
"""

from __future__ import annotations

import random
import string
import sys
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Union

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Factory codes observed on real MacBookAir9,1 units (3 chars)
_FACTORY_CODES: List[str] = ["FVF", "C02", "FVG", "FVH"]

# Year digit for 2020 (MacBookAir9,1 release year)
_YEAR_DIGIT: str = "0"

# Half-year week code characters used by Apple (1-9, C, D, F, G, H, …)
# Letters that Apple uses for week encoding (no vowels)
_WEEK_CODES: str = "123456789CDFGHJKLMNPQRSTVWXYZ"

# Model suffixes known for MacBookAir9,1
_MODEL_CODES: List[str] = ["L0YD", "L0YF", "L0YG"]

# Character set for random segments – uppercase alphanumeric minus vowels
_NO_VOWEL_CHARS: str = "".join(
    ch for ch in string.ascii_uppercase + string.digits if ch not in "AEIOU"
)

# MLB total length
_MLB_LENGTH: int = 17

# ROM byte length (MAC-address-sized)
_ROM_LENGTH: int = 6

# Serial number length
_SERIAL_LENGTH: int = 12


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------


class SMBIOSGenerator:
    """Generate randomised but structurally valid SMBIOS values for
    the **MacBookAir9,1** platform.

    Usage::

        gen = SMBIOSGenerator()
        values = gen.generate()
        print(values["SystemSerialNumber"])
    """

    PRODUCT_NAME: str = "MacBookAir9,1"

    def __init__(self, *, seed: int | None = None) -> None:
        """Initialise the generator.

        Args:
            seed: Optional RNG seed for reproducible output (useful in tests).
        """
        self._rng = random.Random(seed)

    # -- public API ---------------------------------------------------------

    def generate(self) -> Dict[str, Union[str, bytes]]:
        """Return a complete set of SMBIOS values.

        Returns:
            Dictionary with keys ``SystemProductName``, ``SystemSerialNumber``,
            ``MLB``, ``SystemUUID``, and ``ROM``.
        """
        serial = self._generate_serial()
        mlb = self._generate_mlb(serial)
        sys_uuid = self._generate_uuid()
        rom = self._generate_rom()

        return {
            "SystemProductName": self.PRODUCT_NAME,
            "SystemSerialNumber": serial,
            "MLB": mlb,
            "SystemUUID": sys_uuid,
            "ROM": rom,
        }

    def display(self, values: Dict[str, Union[str, bytes]] | None = None) -> None:
        """Pretty-print generated SMBIOS values using Rich."""
        if values is None:
            values = self.generate()
        display_smbios(values)

    # -- internal helpers ---------------------------------------------------

    def _generate_serial(self) -> str:
        """Generate a plausible 12-character Apple serial number.

        Format::

            FFFF 0 W UUU MMMM
            ^^^^ ^ ^ ^^^ ^^^^
            |    | | |   └─ Model code (4 chars)
            |    | | └───── Unique unit id (3 chars, no vowels)
            |    | └─────── Half-year week code (1 char)
            |    └───────── Year digit (1 char, '0' for 2020)
            └────────────── Factory code (4 chars)

        Returns:
            A 12-character uppercase serial string.
        """
        factory: str = self._rng.choice(_FACTORY_CODES)
        year: str = _YEAR_DIGIT
        week: str = self._rng.choice(_WEEK_CODES)
        unit: str = self._rand_chars(3)
        model: str = self._rng.choice(_MODEL_CODES)

        serial = f"{factory}{year}{week}{unit}{model}"

        # Defensive: ensure exactly 12 characters
        if len(serial) != _SERIAL_LENGTH:
            raise ValueError(
                f"Generated serial '{serial}' has length {len(serial)}, "
                f"expected {_SERIAL_LENGTH}"
            )
        return serial

    def _generate_mlb(self, serial: str) -> str:
        """Generate a 17-character Main Logic Board serial.

        The first 3 characters are derived from the serial number's factory
        code; the remaining 14 are random uppercase alphanumeric (no vowels).

        Args:
            serial: A previously generated 12-char serial number.

        Returns:
            A 17-character MLB string.
        """
        prefix: str = serial[:3]
        suffix_len: int = _MLB_LENGTH - len(prefix)
        suffix: str = self._rand_chars(suffix_len)
        mlb = f"{prefix}{suffix}"

        if len(mlb) != _MLB_LENGTH:
            raise ValueError(
                f"Generated MLB '{mlb}' has length {len(mlb)}, "
                f"expected {_MLB_LENGTH}"
            )
        return mlb

    def _generate_uuid(self) -> str:
        """Generate a random v4 UUID string.

        Returns:
            UUID in standard ``8-4-4-4-12`` hexadecimal format (uppercase).
        """
        # Build a v4 UUID from our seeded RNG so that seed-based
        # reproducibility works end-to-end.
        random_bytes = bytes(self._rng.getrandbits(8) for _ in range(16))

        # Set version (4) and variant bits per RFC 4122
        b = bytearray(random_bytes)
        b[6] = (b[6] & 0x0F) | 0x40  # version 4
        b[8] = (b[8] & 0x3F) | 0x80  # variant 1
        generated_uuid = uuid.UUID(bytes=bytes(b))

        return str(generated_uuid).upper()

    def _generate_rom(self) -> bytes:
        """Generate a random 6-byte ROM value (MAC-address-like).

        The first byte has bit 0 cleared (unicast) and bit 1 set (locally
        administered) so it looks like a plausible locally-assigned MAC.

        Returns:
            6 raw bytes.
        """
        rom = bytearray(self._rng.getrandbits(8) for _ in range(_ROM_LENGTH))
        # Locally administered, unicast
        rom[0] = (rom[0] & 0xFE) | 0x02
        return bytes(rom)

    # -- utilities ----------------------------------------------------------

    def _rand_chars(self, length: int) -> str:
        """Return *length* random characters from the no-vowel set."""
        return "".join(self._rng.choices(_NO_VOWEL_CHARS, k=length))


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------


def _mask_serial(serial: str, *, visible: int = 4) -> str:
    """Mask the middle portion of a serial for secure display.

    Shows the first *visible* and last *visible* characters, replacing
    the middle with asterisks.

    Args:
        serial: The full serial string.
        visible: Number of characters to show on each end.

    Returns:
        Partially masked serial string.
    """
    if len(serial) <= visible * 2:
        return serial
    return serial[:visible] + "*" * (len(serial) - visible * 2) + serial[-visible:]


def display_smbios(values: Dict[str, Union[str, bytes]], *, console: Console | None = None) -> None:
    """Pretty-print generated SMBIOS values using Rich.

    Args:
        values: Dictionary returned by :meth:`SMBIOSGenerator.generate`.
        console: Optional Rich console instance.
    """
    con = console or Console()

    table = Table(
        title="Generated SMBIOS Values",
        title_style="bold cyan",
        show_header=True,
        header_style="bold magenta",
        border_style="bright_blue",
        padding=(0, 2),
    )
    table.add_column("Field", style="bold white", min_width=20)
    table.add_column("Value", style="green", min_width=40)

    serial = values["SystemSerialNumber"]
    rom: bytes = values["ROM"]

    table.add_row("SystemProductName", values["SystemProductName"])
    table.add_row(
        "SystemSerialNumber",
        f"{_mask_serial(serial)}  [dim](full length: {len(serial)})[/dim]",
    )
    table.add_row(
        "MLB",
        f"{values['MLB']}  [dim](length: {len(values['MLB'])})[/dim]",
    )
    table.add_row("SystemUUID", values["SystemUUID"])
    table.add_row(
        "ROM",
        f"{rom.hex().upper()}  [dim]({':'.join(f'{b:02X}' for b in rom)})[/dim]",
    )

    con.print()
    con.print(
        Panel(
            table,
            title="[bold green]SMBIOS Generation Complete[/bold green]",
            subtitle=f"[dim]Model: {values['SystemProductName']}[/dim]",
            border_style="green",
            padding=(1, 2),
        )
    )
    con.print()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    console = Console()
    console.print(
        "\n[bold cyan]MacBookAir9,1 SMBIOS Generator[/bold cyan]",
        justify="center",
    )
    console.print(
        "[dim]Generating randomised, structurally valid SMBIOS values…[/dim]\n",
        justify="center",
    )

    generator = SMBIOSGenerator()
    smbios_values = generator.generate()
    display_smbios(smbios_values, console=console)

    # Also show the raw dict for integration reference
    console.print("[bold yellow]Raw dictionary (for config.plist integration):[/bold yellow]")
    for key, value in smbios_values.items():
        if isinstance(value, bytes):
            console.print(f"  [cyan]{key}[/cyan]: {value!r}")
        else:
            console.print(f"  [cyan]{key}[/cyan]: {value!r}")
    console.print()
