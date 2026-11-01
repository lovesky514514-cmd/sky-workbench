; Seed only a fresh profile. API credentials are supplied at runtime.
!macro NSIS_HOOK_POSTINSTALL
  Push $0
  IfFileExists "$LOCALAPPDATA\${BUNDLEID}\settings.json" sky_provider_defaults_done
  CreateDirectory "$LOCALAPPDATA\${BUNDLEID}"
  ClearErrors
  FileOpen $0 "$LOCALAPPDATA\${BUNDLEID}\settings.json" w
  IfErrors sky_provider_defaults_done
  FileWrite $0 '{"schema_version":1,"active_provider":"deepseek","providers":{"deepseek":{"enabled":true,"base_url":"https://api.deepseek.com/v1","model":"deepseek-flash"}}}'
  FileClose $0
sky_provider_defaults_done:
  Pop $0
!macroend
