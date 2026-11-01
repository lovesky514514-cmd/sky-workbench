; Seed a fresh lab profile only. Credentials are supplied by the user at runtime.
!macro NSIS_HOOK_POSTINSTALL
  Push $0
  IfFileExists "$LOCALAPPDATA\${BUNDLEID}\settings.json" sky_lab_defaults_done
  CreateDirectory "$LOCALAPPDATA\${BUNDLEID}"
  ClearErrors
  FileOpen $0 "$LOCALAPPDATA\${BUNDLEID}\settings.json" w
  IfErrors sky_lab_defaults_done
  FileWrite $0 '{"schema_version":1,"active_provider":"deepseek","providers":{"deepseek":{"enabled":true,"base_url":"https://api.deepseek.com/v1","model":"deepseek-flash"}}}'
  FileClose $0
sky_lab_defaults_done:
  Pop $0
!macroend
