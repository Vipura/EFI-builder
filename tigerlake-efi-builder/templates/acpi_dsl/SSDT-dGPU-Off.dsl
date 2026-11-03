DefinitionBlock ("", "SSDT", 2, "LENOVO", "dGPU-Off", 0x00001000)
{
    External (_SB_.PC00.PEG0.PEGP._OFF, MethodObj)
    External (_SB_.PC00.RP01.PXSX._OFF, MethodObj)
    External (_SB_.PC00.RP05.PXSX._OFF, MethodObj)
    External (_SB_.PCI0.PEG0.PEGP._OFF, MethodObj)
    External (_SB_.PCI0.RP01.PXSX._OFF, MethodObj)

    Device (RMD1)
    {
        Name (_HID, "RMD10000")
        Method (_INI, 0, NotSerialized)
        {
            If (CondRefOf (\_SB.PC00.PEG0.PEGP._OFF)) { \_SB.PC00.PEG0.PEGP._OFF () }
            If (CondRefOf (\_SB.PC00.RP01.PXSX._OFF)) { \_SB.PC00.RP01.PXSX._OFF () }
            If (CondRefOf (\_SB.PC00.RP05.PXSX._OFF)) { \_SB.PC00.RP05.PXSX._OFF () }
            If (CondRefOf (\_SB.PCI0.PEG0.PEGP._OFF)) { \_SB.PCI0.PEG0.PEGP._OFF () }
            If (CondRefOf (\_SB.PCI0.RP01.PXSX._OFF)) { \_SB.PCI0.RP01.PXSX._OFF () }
        }
    }
}
