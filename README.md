# TigerLake-EFI-Builder

Automated OpenCore EFI folder generator for **Lenovo IdeaPad Slim 5 (82FG)** with 11th Gen Intel Core i5-1135G7 and Intel Iris Xe Graphics (`9A49`).

## Key Features
- Automated hardware scanner via Windows WMI & Registry
- Automatic GitHub release fetching for OpenCore 1.0.8 & essential kexts
- WhateverXe.kext + AMFIPass Mod v1.4.1 for Tiger Lake Xe graphics
- Strict assertion guard ensuring WhateverGreen is never bundled
- Disables discrete Nvidia MX450 dGPU via SSDT-dGPU-Off
- Compiles 6 ACPI SSDTs using Intel iASL
- Validated against ocvalidate with 0 errors
