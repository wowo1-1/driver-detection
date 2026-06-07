Set WshShell = CreateObject("WScript.Shell")
projectDir = "D:\ai\pilaojiance\Yolov5-deepsort-driverDistracted-driving-behavior-detection"
pythonExe = projectDir & "\venv\Scripts\python.exe"

' start server
WshShell.Run pythonExe & " " & projectDir & "\server.py", 0, False
WScript.Sleep 2000

' start boss
WshShell.Run pythonExe & " " & projectDir & "\boss_app.py", 1, False

MsgBox "Server + Boss started!" & vbCrLf & vbCrLf & "1. Run run.bat" & vbCrLf & "2. Menu -> Boss Server Settings" & vbCrLf & "3. Enable + Name + Save", vbInformation, "Driver Detection"
