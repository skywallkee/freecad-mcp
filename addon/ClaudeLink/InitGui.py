# ClaudeLink auto-start: runs once when the FreeCAD GUI starts.
# (InitGui.py is exec'd by FreeCAD without __file__; the Mod folder is already on sys.path.)
def _claudelink_autostart():
    try:
        import claudelink
        claudelink.start()
    except Exception as e:
        import FreeCAD
        FreeCAD.Console.PrintError("ClaudeLink failed to start: %s\n" % e)


try:
    from PySide import QtCore as _QtCore
    _QtCore.QTimer.singleShot(1000, _claudelink_autostart)
except Exception:
    _claudelink_autostart()
