# -*- coding: utf-8 -*-
"""Markdown 渲染预览视图（基于 QWebEngineView）。

预览区使用 Chromium 内核渲染标准 markdown；点击句子通过自定义 mdtts:N 协议回传
Python，当前句高亮由前端 JS 完成。按要求对外暴露 MarkdownView，内部页面由 _Page 承载。
"""
import os

from PyQt5.QtCore import QUrl, QSize, pyqtSignal
from PyQt5.QtWebEngineWidgets import QWebEngineView, QWebEnginePage
from PyQt5.QtWidgets import QMessageBox


class _Page(QWebEnginePage):
    """拦截自定义 mdtts:N 协议，把点击的句子 idx 回传给 Python。"""
    sentenceClicked = pyqtSignal(int)
    renderError = pyqtSignal(str)

    def acceptNavigationRequest(self, url, _type, isMainFrame):
        if url.scheme() == 'mdtts':
            try:
                self.sentenceClicked.emit(int(url.path()))
            except Exception:
                pass
            return False
        # 允许本地文件（图片/资源）与常用 http(s) 链接
        return super().acceptNavigationRequest(url, _type, isMainFrame)

    def renderProcessTerminated(self, terminationType, exitCode, exitReason):
        # Chromium 渲染子进程崩溃（最常见：被沙箱/杀软拦截白屏）。
        # 弹窗 + 写日志，避免用户只看到一片空白不知所措。
        msg = ("预览渲染进程异常退出（类型=%s，退出码=%s）：%s"
               % (terminationType, exitCode, exitReason))
        self.renderError.emit(msg)


class MarkdownView(QWebEngineView):
    """标准 markdown 渲染视图：点击句子定位、当前句高亮。"""
    sentenceClicked = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._page = _Page(self)
        self.setPage(self._page)
        self._page.sentenceClicked.connect(self.sentenceClicked)
        self._page.renderError.connect(self._on_render_error)
        self._last_idx = -1
        self._page.loadFinished.connect(self._on_load)

    def sizeHint(self):
        # QWebEngineView 默认 sizeHint 为 0x0，放进布局会被压成 0 宽，
        # 导致预览面板"消失"。给定合理尺寸修正。
        return QSize(560, 700)

    def _on_render_error(self, msg):
        try:
            log = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "webengine_error.log")
            with open(log, "a", encoding="utf-8") as f:
                f.write(msg + "\n")
        except Exception:
            pass
        QMessageBox.warning(self, "预览渲染失败",
                            msg + "\n\n详细已记录到 webengine_error.log")

    def _on_load(self, ok):
        if ok and self._last_idx >= 0:
            self.page().runJavaScript("highlightSentence(%d)" % self._last_idx)

    def set_content(self, html, base_dir=''):
        base = QUrl.fromLocalFile(base_dir + os.sep) if base_dir else QUrl()
        self._last_idx = -1
        self.setHtml(html, base)

    def highlight(self, idx):
        self._last_idx = idx
        self.page().runJavaScript("highlightSentence(%d)" % idx)

    def clear_highlight(self):
        self._last_idx = -1
        self.page().runJavaScript("clearHighlight()")