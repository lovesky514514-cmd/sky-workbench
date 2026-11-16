LangString SkyWebView2Unavailable 1033 "SKY Workbench cannot detect a usable Microsoft Edge WebView2 Runtime for this Windows user. Repair WebView2, then run this installer again."
LangString SkyWebView2Unavailable 2052 "SKY Workbench 无法为当前 Windows 用户检测到可用的 Microsoft Edge WebView2 Runtime。请修复 WebView2 后重新运行安装程序。"

!macro NSIS_HOOK_PREINSTALL
  ; 用产品探针校验依赖 / Verify the dependency through the desktop executable.
  InitPluginsDir
  File "/oname=$PLUGINSDIR\sky-webview2-check.exe" "${MAINBINARYSRCPATH}"
  ClearErrors
  ExecWait '"$PLUGINSDIR\sky-webview2-check.exe" --check-webview2' $0
  ${If} ${Errors}
    StrCpy $0 2
  ${EndIf}
  Delete "$PLUGINSDIR\sky-webview2-check.exe"
  ${If} $0 != 0
    DetailPrint "$(SkyWebView2Unavailable)"
    MessageBox MB_OK|MB_ICONSTOP "$(SkyWebView2Unavailable)" /SD IDOK
    SetErrorLevel 2
    Abort
  ${EndIf}
!macroend
