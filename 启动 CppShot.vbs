Option Explicit
Dim shell, fso, scriptDir, exe, appDir, env
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)

If fso.FileExists(scriptDir & "\CppShot.exe") Then
    exe = scriptDir & "\CppShot.exe"
ElseIf fso.FileExists(scriptDir & "\CppShot-Portable\CppShot.exe") Then
    exe = scriptDir & "\CppShot-Portable\CppShot.exe"
ElseIf fso.FileExists(scriptDir & "\dist\CppShot\CppShot.exe") Then
    exe = scriptDir & "\dist\CppShot\CppShot.exe"
Else
    MsgBox "CppShot.exe was not found." & vbCrLf & vbCrLf & _
           "Please extract CppShot-1.0.0-Windows-Portable.zip, then run the launcher inside that folder.", _
           vbExclamation, "CppShot"
    WScript.Quit 1
End If

appDir = fso.GetParentFolderName(exe)
shell.CurrentDirectory = appDir
Set env = shell.Environment("Process")
env("CPPSHOT_HOME") = appDir & "\data"
env("CPPSHOT_SCREENSHOT_DIR") = appDir & "\screenshots"
env("CPPSHOT_BUILD_DIR") = appDir & "\build"
env("TMP") = appDir & "\tmp"
env("TEMP") = appDir & "\tmp"
env("TMPDIR") = appDir & "\tmp"
shell.Run """" & exe & """", 0, False
