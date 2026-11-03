DefinitionBlock ("", "SSDT", 2, "CORP", "EC", 0x00001000)
{
    External (_SB_.PCI0.LPCB, DeviceObj)
    External (_SB_.PC00.LPCB, DeviceObj)

    If (_OSI ("Darwin"))
    {
        Scope (\_SB)
        {
            Device (USBX)
            {
                Name (_ADR, Zero)
                Method (_DSM, 4, NotSerialized)
                {
                    If (LNot (Arg2))
                    {
                        Return (Buffer (One) { 0x03 })
                    }
                    Return (Package (0x08)
                    {
                        "kUSBSleepPowerSupply", 0x13EC,
                        "kUSBSleepPortCurrentLimit", 0x0834,
                        "kUSBWakePowerSupply", 0x13EC,
                        "kUSBWakePortCurrentLimit", 0x0834
                    })
                }
            }

            Device (EC)
            {
                Name (_HID, "ACID0001")
                Method (_STA, 0, NotSerialized)
                {
                    If (_OSI ("Darwin"))
                    {
                        Return (0x0F)
                    }
                    Else
                    {
                        Return (Zero)
                    }
                }
            }
        }
    }
}
