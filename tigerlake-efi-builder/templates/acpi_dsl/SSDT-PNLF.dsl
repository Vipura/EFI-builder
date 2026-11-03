DefinitionBlock ("", "SSDT", 2, "CORP", "PNLF", 0x00000000)
{
    External (_SB_.PC00.GFX0, DeviceObj)

    If (_OSI ("Darwin"))
    {
        Scope (\_SB.PC00.GFX0)
        {
            Device (PNLF)
            {
                Name (_HID, EisaId ("APP0002"))
                Name (_CID, "backlight")
                Name (_UID, 0x13)
                Name (_STA, 0x0B)
            }
        }
    }
}
