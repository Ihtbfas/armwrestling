"""Preflight for the Windows STM32 debug launcher."""
import sys


def tk_error(error):
    print('无法加载 tkinter 或初始化 Tcl。请修复 Python 安装，勾选 Tcl/Tk 组件。\n'
          '详细信息  {}'.format(error))
    return 1


def main():
    print('使用 Python {}\n  {}'.format(sys.version.split()[0], sys.executable))
    if sys.version_info < (3, 10):
        print('需要 Python 3.10 或更新版本。请安装新版 Python，然后用它重建项目的 .venv。')
        return 1
    try:
        import tkinter
    except (ImportError, OSError) as error:
        return tk_error(error)
    try:
        tcl_version = tkinter.Tcl().eval('info patchlevel')
    except (tkinter.TclError, OSError) as error:
        return tk_error(error)
    try:
        import serial
        from serial import Serial
        from serial.tools.list_ports import comports
    except (ImportError, OSError) as error:
        print('当前 Python 缺少可用的 pyserial。请在 PowerShell 执行：\n'
              '  & "{}" -m pip install pyserial\n'
              '详细信息  {}'.format(sys.executable, error))
        return 1
    print('Tcl {} · pyserial {} · 启动 STM32 联调助手'.format(
        tcl_version, getattr(serial, '__version__', '版本未知')))
    return 0


if __name__ == '__main__':
    exit_code = main()
    if exit_code == 0 and sys.argv[1:] == ['--launch']:
        from tools.stm32_debug import main as launch
        launch()
    sys.exit(exit_code)
