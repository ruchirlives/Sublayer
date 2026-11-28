!macro NSIS_HOOK_POSTINSTALL
  ; Create Start Menu shortcut to re-run the optional runtime installer anytime
  CreateShortCut "$SMPROGRAMS\${MAINBINARYNAME}\Sublayer Runtime Setup.lnk" "powershell.exe" '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "$INSTDIR\scripts\installer-runtimes.ps1" -InstallRoot "$INSTDIR\runtimes"' "$INSTDIR\${MAINBINARYNAME}.exe" 0

  ; Launch the optional runtime setup wizard during installation (non-fatal if user closes/skips)
  IfSilent 0 +3
    ExecWait 'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "$INSTDIR\scripts\installer-runtimes.ps1" -InstallRoot "$INSTDIR\runtimes" -Silent' $0
    Goto runtime_setup_done
  ExecWait 'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "$INSTDIR\scripts\installer-runtimes.ps1" -InstallRoot "$INSTDIR\runtimes"'
  runtime_setup_done:
!macroend

!macro NSIS_HOOK_POSTUNINSTALL
  Delete "$SMPROGRAMS\${MAINBINARYNAME}\Sublayer Runtime Setup.lnk"
  RMDir /r "$INSTDIR\runtimes"
!macroend
