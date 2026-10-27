' SPDX-License-Identifier: MIT
' Launch only this bridge's watchdog without creating a visible console.
' Wait for completion so Task Scheduler can enforce IgnoreNew and report exit.
Option Explicit
Dim shell, files, script, powershell, command, result
Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")
script = files.BuildPath(files.GetParentFolderName(WScript.ScriptFullName), "watchdog-once.ps1")
If Not files.FileExists(script) Then WScript.Quit 2
powershell = shell.ExpandEnvironmentStrings("%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe")
command = Chr(34) & powershell & Chr(34) & " -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File " & Chr(34) & script & Chr(34)
result = shell.Run(command, 0, True)
WScript.Quit result
