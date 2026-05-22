# runtime/flexlib

This directory holds `FlexLib.dll` and its dependencies, loaded at runtime by
`pythonnet`. **The DLLs are not redistributed in this repo** — they ship with
SmartSDR for Windows. Run `scripts\package_flexlib.ps1` to copy them out of
your local SmartSDR install:

```powershell
.\scripts\package_flexlib.ps1
# Or override the SmartSDR root:
.\scripts\package_flexlib.ps1 -SmartSdrRoot "D:\FlexRadio Systems"
```

The script also `Unblock-File`s each DLL so .NET will load it.

If `Flex.Smoothlake.FlexLib` namespace import fails, double-check:
1. The DLL is here (`FlexLib.dll`, `Util.dll`, `Vita.dll`).
2. The DLLs are unblocked (Right-click → Properties → Unblock).
3. The pythonnet runtime matches the FlexLib target framework
   (.NET Framework 4.7.2 vs. .NET 6). See `runtime/flexlib/notes.md` after first
   successful load.
