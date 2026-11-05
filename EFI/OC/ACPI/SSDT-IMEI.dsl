/*
 * SSDT-IMEI — Intel Management Engine Interface stub for Tiger Lake
 * Required for AppleIntelTGLGraphics firmware validation path on macOS Tahoe.
 *
 * Generated for: Lenovo IdeaPad Slim 5 / i5-1135G7 (TGL-U)
 * IMEI is at PCI 0:16.0 on Tiger Lake.
 */
DefinitionBlock ("", "SSDT", 2, "HACK", "IMEI", 0x00001000)
{
    External (_SB_.PCI0, DeviceObj)

    Scope (_SB.PCI0)
    {
        Device (IMEI)
        {
            Name (_ADR, 0x00160000)  // PCI Address: Dev 0x16, Func 0x00
            Method (_STA, 0, NotSerialized)
            {
                Return (0x0F)         // Device present, enabled, visible
            }
        }
    }
}

