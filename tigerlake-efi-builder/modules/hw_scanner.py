"""Hardware Scanner Module for Tiger Lake EFI Builder."""

from rich.console import Console
console = Console(legacy_windows=False)

def scan():
    console.print("[cyan]Scanning host hardware...[/cyan]")
    return {}
