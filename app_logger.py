# -*- coding: utf-8 -*-
"""应用运行时日志。

打包成 .app 后，--windowed 模式会丢弃 stdout/stderr，出 bug 时无从排查。
本模块用 logging + RotatingFileHandler 把关键操作与异常写到固定文件，
便于事后读取定位。

日志位置（Mac）：~/Library/Logs/MDReader/app.log
日志位置（其他）：可执行文件同目录下的 logs/app.log
单文件 1MB，保留 3 个历史备份，避免无限增长。
"""
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

_APP_NAME = "MDReader"


def _log_dir():
    """返回日志目录，优先用 macOS 标准位置 ~/Library/Logs/<APP>/。"""
    if sys.platform == 'darwin':
        base = os.path.expanduser('~/Library/Logs')
    else:
        # 打包后 sys.executable 指向 .exe；开发期指 .py，回退到脚本目录
        base = os.path.dirname(os.path.abspath(sys.executable))
        if not os.path.isdir(base):
            base = os.getcwd()
    d = os.path.join(base, _APP_NAME)
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        # 目录创建失败时回退到临时目录，保证日志一定能写
        import tempfile
        d = os.path.join(tempfile.gettempdir(), _APP_NAME)
        os.makedirs(d, exist_ok=True)
    return d


def log_path():
    """返回当前日志文件的完整路径。"""
    return os.path.join(_log_dir(), 'app.log')


def setup_logger():
    """初始化并返回全局 logger。重复调用安全（只配置一次）。"""
    lg = logging.getLogger(_APP_NAME)
    if lg.handlers:
        return lg
    lg.setLevel(logging.DEBUG)

    fmt = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S')
    try:
        fh = RotatingFileHandler(
            log_path(), maxBytes=1_000_000, backupCount=3, encoding='utf-8')
        fh.setFormatter(fmt)
        lg.addHandler(fh)
    except Exception:
        # 文件 handler 失败时退化为 stderr，绝不让日志拖垮应用
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        lg.addHandler(sh)

    # 捕获未处理的异常，写日志后再交给默认处理器
    def _excepthook(exc_type, exc_value, exc_tb):
        lg.critical("未处理异常（主线程）", exc_info=(exc_type, exc_value, exc_tb))
        sys.__excepthook__(exc_type, exc_value, exc_tb)
    sys.excepthook = _excepthook

    # 捕获子线程（QThread / threading.Thread）的未处理异常。
    # sys.excepthook 只管主线程；子线程异常默认打印到 stderr，
    # --windowed 模式下 stderr 被丢弃，会完全静默导致"播放突然停了"却无日志。
    import threading
    _orig_threading_excepthook = threading.excepthook

    def _threading_excepthook(args):
        lg.critical("未处理异常（子线程 %s）: %s",
                    args.thread.name, args.exc_value,
                    exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
    threading.excepthook = _threading_excepthook

    return lg


logger = setup_logger()
