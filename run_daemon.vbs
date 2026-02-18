Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "C:\Users\magi1\学習\autonomous-earner"
WshShell.Run "pythonw ""C:\Users\magi1\学習\autonomous-earner\daemon.py""", 0, False
