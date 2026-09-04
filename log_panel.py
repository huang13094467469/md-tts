# -*- coding: utf-8 -*-
"""运行日志 -> GUI 面板桥接与折叠日志面板控件。

把 Python logging（含播放等工作线程产生的日志）转发到主线程的 GUI 面板：
  - _LogBridge（QObject + 信号）用 QueuedConnection 天然跨线程安全；
  - get_gui_log_handler() 返回一个可挂到全局 logger 的 Handler；
  - LogPanel 是可折叠的只读日志控件（展开/收起、清空、自动滚底、限行防内存膨胀）。
"""
import logging

from PyQt5.QtCore import QObject, pyqtSignal
from PyQt5.QtWidgets import QFrame, QHBoxLayout, QPushButton, QPlainTextEdit, QVBoxLayout


class _LogBridge(QObject):
    """把任意线程的日志文本转发到主线程（信号默认 QueuedConnection）。"""
    newLog = pyqtSignal(str)


_log_bridge = _LogBridge()


class _GuiLogHandler(logging.Handler):
    """把每条日志格式化后经信号转发给主线程。"""

    def emit(self, record):
        try:
            _log_bridge.newLog.emit(self.format(record))
        except Exception:
            pass


def get_gui_log_handler():
    """返回一个格式化好的 GUI 日志 Handler，调用方负责挂到 logger。"""
    handler = _GuiLogHandler()
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S"))
    return handler


class LogPanel(QFrame):
    """底部可折叠的运行日志面板。

    使用方式（在 MainWindow 中）：
        self.log_panel = LogPanel()
        _log_bridge.newLog.connect(self.log_panel.append_log)
        logger.addHandler(get_gui_log_handler())
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.StyledPanel)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 4, 12, 8)
        lay.setSpacing(4)

        # 行头条：展开按钮 + 清空按钮
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        self.btn_toggle = QPushButton("▸ 运行日志")
        self.btn_toggle.setCheckable(True)
        self.btn_toggle.setChecked(False)
        self.btn_toggle.setFlat(True)
        self.btn_toggle.setStyleSheet(
            "QPushButton{border:none; color:#555; font-weight:bold;} "
            "QPushButton:hover{color:#0969da;}")
        self.btn_toggle.clicked.connect(self._toggle)
        head.addWidget(self.btn_toggle)
        head.addStretch(1)
        self.btn_clear = QPushButton("清空")
        self.btn_clear.setFixedHeight(22)
        self.btn_clear.setEnabled(False)
        head.addWidget(self.btn_clear)
        lay.addLayout(head)

        # 文本区
        self._log_edit = QPlainTextEdit()
        self._log_edit.setReadOnly(True)
        self._log_edit.setMaximumHeight(180)
        self._log_edit.setStyleSheet(
            "QPlainTextEdit{background:#f6f8fa; color:#24292f;"
            "border:1px solid #d0d7de; border-radius:4px;"
            "font-family:Consolas,'Courier New',monospace; font-size:12px;}")
        self._log_edit.setVisible(False)
        lay.addWidget(self._log_edit)

        # _log_edit 创建后连接清空按钮
        self.btn_clear.clicked.connect(self._log_edit.clear)

    def _toggle(self):
        show = self.btn_toggle.isChecked()
        self._log_edit.setVisible(show)
        self.btn_toggle.setText("▾ 运行日志" if show else "▸ 运行日志")
        if show and self._log_edit.document().blockCount() > 0:
            self._log_edit.verticalScrollBar().setValue(
                self._log_edit.verticalScrollBar().maximum())

    def append_log(self, text):
        """在面板追加一行日志（主线程调用）。"""
        self._log_edit.appendPlainText(text)
        # 限行：避免长时间运行占用过多内存
        if self._log_edit.document().blockCount() > 2000:
            cursor = self._log_edit.textCursor()
            cursor.select(cursor.Document)
            cursor.removeSelectedText()
        self.btn_clear.setEnabled(True)
        if self._log_edit.isVisible():
            self._log_edit.verticalScrollBar().setValue(
                self._log_edit.verticalScrollBar().maximum())