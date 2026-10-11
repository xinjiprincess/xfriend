Unicode true

# Octop desktop NSIS installer.
# Built by `wails3 task package` on a Windows runner:
#   makensis -DARG_WAILS_AMD64_BINARY=..\..\..\bin\Octop.exe project.nsi
#   makensis -DARG_WAILS_ARM64_BINARY=..\..\..\bin\Octop.exe project.nsi

# Written by stamp_version.py nsis-defines (pep440 display + X.X.X.X file version).
!include /nonfatal "version_defines.nsh"
!include "wails_tools.nsh"

SetCompressor /SOLID lzma

# VI*Version must be numeric X.X.X.X. Display strings may be pep440 (e.g. 1.0.2b1).
!ifndef INFO_FILEVERSION
    !define INFO_FILEVERSION "${INFO_PRODUCTVERSION}.0"
!endif
VIProductVersion "${INFO_FILEVERSION}"
VIFileVersion    "${INFO_FILEVERSION}"

VIAddVersionKey "CompanyName"     "${INFO_COMPANYNAME}"
VIAddVersionKey "FileDescription" "${INFO_PRODUCTNAME} Installer"
VIAddVersionKey "ProductVersion"  "${INFO_PRODUCTVERSION}"
VIAddVersionKey "FileVersion"     "${INFO_PRODUCTVERSION}"
VIAddVersionKey "LegalCopyright"  "${INFO_COPYRIGHT}"
VIAddVersionKey "ProductName"     "${INFO_PRODUCTNAME}"

ManifestDPIAware true

!include "MUI.nsh"

!define MUI_ICON "..\icon.ico"
!define MUI_UNICON "..\icon.ico"
!define MUI_FINISHPAGE_NOAUTOCLOSE
!define MUI_ABORTWARNING
!define MUI_FINISHPAGE_RUN "$INSTDIR\${PRODUCT_EXECUTABLE}"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!define MUI_PAGE_CUSTOMFUNCTION_SHOW ShowInstallMascot
!define MUI_PAGE_CUSTOMFUNCTION_LEAVE StopInstallMascot
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "SimpChinese"
!insertmacro MUI_LANGUAGE "English"
!insertmacro MUI_RESERVEFILE_LANGDLL

LangString AppStillRunning ${LANG_SIMPCHINESE} "请先退出正在运行的 Singu Xfriend（包括系统托盘中的程序），再点击重试。安装程序会保留您的账号、对话和工作区数据。"
LangString AppStillRunning ${LANG_ENGLISH} "Close Singu Xfriend, including its system tray window, then click Retry. Your account, conversations and workspace data will be preserved."

Name "${INFO_PRODUCTNAME}"
!ifndef INSTALLER_OUTFILE
    !define INSTALLER_OUTFILE "..\..\..\bin\${INFO_PROJECTNAME}-desktop-windows-${ARCH}-${INFO_PRODUCTVERSION}.exe"
!endif
OutFile "${INSTALLER_OUTFILE}"
!if "${WAILS_INSTALL_SCOPE}" == "user"
    InstallDir "$LOCALAPPDATA\Programs\${INFO_PRODUCTNAME}"
!else
    InstallDir "$PROGRAMFILES64\${INFO_PRODUCTNAME}"
!endif
ShowInstDetails show

Var MascotControl

Function ShowInstallMascot
    InitPluginsDir
    File /oname=$PLUGINSDIR\mascot.avi "mascot.avi"
    # SysAnimate32 plays uncompressed AVI on its own timer while files unpack.
    System::Call 'comctl32::InitCommonControls()'
    System::Call 'user32::CreateWindowExW(i 0, w "SysAnimate32", w "", i 0x50000002, i 0, i 0, i 80, i 64, p $HWNDPARENT, p 0, p 0, p 0) p.r0'
    StrCpy $MascotControl $0
    # Place the animation in the header, allowing for the current DPI.
    # A right-aligned child avoids covering the installation description.
    System::Alloc 16
    Pop $1
    System::Call 'user32::GetClientRect(p $HWNDPARENT, p r1)'
    System::Call '*$1(i, i, i.r2, i.r3)'
    System::Free $1
    IntOp $2 $2 - 92
    System::Call 'user32::SetWindowPos(p $MascotControl, p 0, i r2, i 8, i 80, i 64, i 0x14)'
    SendMessage $MascotControl 0x0467 0 "STR:$PLUGINSDIR\mascot.avi"
    SendMessage $MascotControl 0x0465 -1 0xFFFF0000
FunctionEnd

Function StopInstallMascot
    SendMessage $MascotControl 0x0466 0 0
    System::Call 'user32::DestroyWindow(p $MascotControl)'
FunctionEnd

Function .onInit
    IfSilent skipLang
    !insertmacro MUI_LANGDLL_DISPLAY
    skipLang:
    !insertmacro wails.checkArchitecture
FunctionEnd

Function CheckApplicationClosed
    retryApplication:
    IfFileExists "$INSTDIR\${PRODUCT_EXECUTABLE}" 0 applicationClosed
    ClearErrors
    FileOpen $0 "$INSTDIR\${PRODUCT_EXECUTABLE}" a
    IfErrors applicationRunning
    FileClose $0
    Goto applicationClosed
    applicationRunning:
    IfSilent cancelInstall
    MessageBox MB_RETRYCANCEL|MB_ICONEXCLAMATION "$(AppStillRunning)" IDRETRY retryApplication
    cancelInstall:
    SetErrorLevel 2
    Quit
    applicationClosed:
FunctionEnd

Section
    !insertmacro wails.setShellContext

    Call CheckApplicationClosed

    !insertmacro wails.webview2runtime

    SetOutPath $INSTDIR

    !insertmacro wails.files

    CreateShortcut "$SMPROGRAMS\${INFO_PRODUCTNAME}.lnk" "$INSTDIR\${PRODUCT_EXECUTABLE}"
    CreateShortcut "$DESKTOP\${INFO_PRODUCTNAME}.lnk" "$INSTDIR\${PRODUCT_EXECUTABLE}"

    !insertmacro wails.associateFiles
    !insertmacro wails.associateCustomProtocols

    !insertmacro wails.writeUninstaller
SectionEnd

Section "uninstall"
    !insertmacro wails.setShellContext

    RMDir /r "$AppData\${PRODUCT_EXECUTABLE}"

    RMDir /r $INSTDIR

    Delete "$SMPROGRAMS\${INFO_PRODUCTNAME}.lnk"
    Delete "$DESKTOP\${INFO_PRODUCTNAME}.lnk"

    !insertmacro wails.unassociateFiles
    !insertmacro wails.unassociateCustomProtocols

    !insertmacro wails.deleteUninstaller
SectionEnd
