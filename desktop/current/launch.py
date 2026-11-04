"""Dependency checks before importing the GUI. No profiles or sockets at startup."""
from pathlib import Path
import importlib
import sys
import traceback
from datetime import datetime

ROOT=Path(__file__).resolve().parent

def notify(message):
    print(message,flush=True)
    if sys.platform=='win32':
        import ctypes
        ctypes.windll.user32.MessageBoxW(None,message,'青健智课 · 启动检查',0x10)

def main():
    if sys.version_info<(3,10):
        notify('需要 Python 3.10 或以上版本。\n当前解释器：'+sys.executable);return 2
    missing=[]
    for package in ('PySide6.QtCore','PySide6.QtGui','PySide6.QtWidgets'):
        try:importlib.import_module(package)
        except Exception as e:missing.append(package+'：'+str(e))
    if missing:
        notify('缺少必要依赖，或依赖无法加载：\n'+'\n'.join(missing)+'\n\n解释器：'+sys.executable+'\n请在此解释器中安装 requirements.txt 所列依赖。');return 2
    if '--check' in sys.argv:
        print('启动依赖检查通过；未打开界面、未请求网络。\nPython: '+sys.executable,flush=True);return 0
    try:
        from app import main as open_app
        return open_app()
    except Exception:
        message=traceback.format_exc()
        log=ROOT/'logs'/'startup_error.log'
        try:
            log.parent.mkdir(parents=True,exist_ok=True)
            with log.open('a',encoding='utf-8') as f:f.write(datetime.now().isoformat()+'\n'+message+'\n')
        except OSError:pass
        notify('启动失败。\n详细日志：'+str(log)+'\n\n'+message[-1200:]);return 1

if __name__=='__main__':raise SystemExit(main())
