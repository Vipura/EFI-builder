DefinitionBlock ("", "SSDT", 2, "CORP", "XOSI", 0x00001000)
{
    Method (XOSI, 1, NotSerialized)
    {
        Local0 = Package (0x02)
        {
            "Windows 2015",
            "Windows 2019"
        }

        If (_OSI ("Darwin"))
        {
            Local1 = SizeOf (Local0)
            Local2 = Zero
            While (Local2 < Local1)
            {
                If (Arg0 == DerefOf (Local0 [Local2]))
                {
                    Return (Ones)
                }
                Local2++
            }
        }
        Return (Zero)
    }
}
