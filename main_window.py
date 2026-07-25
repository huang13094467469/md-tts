# -*- coding: utf-8 -*-
"""MD 朗读工具主界面：加载 MD -> 标准 markdown 渲染预览 -> 点击句子定位朗读 -> 在线/本地双引擎。

预览区使用 QWebEngineView（Chromium 内核），标准 markdown 渲染、图片自适应不跳动；
点击句子通过自定义 mdtts:N 协议回传 Python，当前句高亮由前端 JS 完成。
"""
import os
import sys

# 必须在 import QtWebEngineWidgets 之前设置，否则 Chromium 子进程在受限/
# 沙箱环境下启动失败会导致预览区白屏（Windows 上最常见根因；Mac 上一般无害）
os.environ.setdefault('QTWEBENGINE_DISABLE_SANDBOX', '1')

# 平台感知字体：Mac 用系统原生字体，Windows 保持原配置
IS_MAC = sys.platform == 'darwin'
_UI_FONT = 'PingFang SC' if IS_MAC else 'Microsoft YaHei'

from PyQt5.QtCore import Qt, QUrl, QSize, pyqtSignal
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QComboBox, QSlider, QLabel, QFileDialog, QMessageBox,
    QFrame, QPushButton, QInputDialog,
)
from PyQt5.QtGui import QFont
from PyQt5.QtWebEngineWidgets import QWebEngineView, QWebEnginePage

from md_parser import parse_markdown, parse_to_blocks, preprocess_mdx
from markdown_render import render_markdown
from tts_engine import TTSPlayer, get_local_chinese_voice


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


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.units = []
        self.player = TTSPlayer()
        self.current_idx = -1
        self.paused = False
        self._md_text = ''
        self._md_dir = ''
        self._init_ui()
        self._init_voices()

    # ---------------- UI ----------------
    def _init_ui(self):
        self.setWindowTitle("MD 朗读工具 · TTS Reader")
        self.resize(1200, 780)

        menubar = self.menuBar()
        file_menu = menubar.addMenu("文件")
        open_act = file_menu.addAction("打开 MD/MDX 文件")
        open_act.triggered.connect(self.open_file)
        dir_menu = menubar.addMenu("示例")
        dir_menu.addAction("加载工作区 MD 列表").triggered.connect(self.load_workspace_md)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 10, 10, 10)

        info = QHBoxLayout()
        self.lbl_file = QLabel("未加载文件")
        self.lbl_file.setStyleSheet("color:#555;")
        self.lbl_count = QLabel("")
        self.lbl_count.setStyleSheet("color:#888;")
        info.addWidget(self.lbl_file, 1)
        info.addWidget(self.lbl_count, 0)
        root.addLayout(info)

        self.view = MarkdownView()
        self.view.sentenceClicked.connect(self.on_sentence_clicked)
        root.addWidget(self.view, 1)

        ctrl = QFrame()
        ctrl.setFrameShape(QFrame.StyledPanel)
        cl = QVBoxLayout(ctrl)
        cl.setContentsMargins(12, 10, 12, 10)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("音色:"))
        self.cb_voice = QComboBox()
        self.cb_voice.setMinimumWidth(260)
        self.cb_voice.currentIndexChanged.connect(self.on_param_changed)
        row1.addWidget(self.cb_voice)

        row1.addWidget(QLabel("  语速:"))
        self.slider_rate = QSlider(Qt.Horizontal)
        self.slider_rate.setRange(-50, 100)
        self.slider_rate.setValue(40)
        self.slider_rate.setTickPosition(QSlider.TicksBelow)
        self.slider_rate.setFixedWidth(220)
        self.slider_rate.valueChanged.connect(self.on_rate_changed)
        row1.addWidget(self.slider_rate)
        self.lbl_rate = QLabel("+40%")
        self.lbl_rate.setFixedWidth(48)
        row1.addWidget(self.lbl_rate)
        row1.addStretch(1)
        cl.addLayout(row1)

        row2 = QHBoxLayout()
        self.btn_play = QPushButton("▶ 从头播放")
        self.btn_play.setFixedHeight(36)
        self.btn_play.clicked.connect(self.on_play_from_start)
        self.btn_pause = QPushButton("⏸ 暂停")
        self.btn_pause.setFixedHeight(36)
        self.btn_pause.setEnabled(False)
        self.btn_pause.clicked.connect(self.on_pause_resume)
        self.btn_stop = QPushButton("⏹ 停止")
        self.btn_stop.setFixedHeight(36)
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.on_stop)
        row2.addWidget(self.btn_play)
        row2.addWidget(self.btn_pause)
        row2.addWidget(self.btn_stop)
        row2.addStretch(1)
        self.lbl_status = QLabel("就绪")
        self.lbl_status.setStyleSheet("color:#666;")
        row2.addWidget(self.lbl_status)
        cl.addLayout(row2)

        root.addWidget(ctrl)

    # ---------------- 音色列表 ----------------
    def _init_voices(self):
        self.cb_voice.clear()
        # 在线：仅保留晓晓
        self.cb_voice.addItem("晓晓 (在线·女声)",
                              {'engine': 'online', 'id': 'zh-CN-XiaoxiaoNeural'})
        # 本地：仅保留一个系统中文语音
        cv = get_local_chinese_voice()
        if cv:
            self.cb_voice.addItem(cv[0], {'engine': 'local', 'id': cv[1]})
        else:
            self.cb_voice.addItem("（未检测到系统中文语音）")
            self._disable_item(self.cb_voice.count() - 1)

        self.cb_voice.setCurrentIndex(0)

    def _disable_item(self, idx):
        self.cb_voice.setItemData(idx, None)
        self.cb_voice.model().item(idx).setEnabled(False)

    # ---------------- 数据 ----------------
    def load_workspace_md(self):
        parent = os.path.dirname(os.path.abspath(__file__))
        parent = os.path.dirname(parent)
        md_files = [os.path.join(parent, n) for n in os.listdir(parent)
                    if n.lower().endswith(('.md', '.mdx')) and os.path.isfile(os.path.join(parent, n))]
        if not md_files:
            QMessageBox.information(self, "提示", "工作区未找到 .md / .mdx 文件")
            return
        names = [os.path.basename(f) for f in md_files]
        name, ok = QInputDialog.getItem(self, "选择 MD 文件", "工作区文件:", names, 0, False)
        if ok:
            self.open_path(md_files[names.index(name)])

    def open_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "打开 Markdown 文件", "",
            "Markdown / MDX (*.md *.mdx);;Markdown (*.md);;MDX (*.mdx);;All (*.*)")
        if path:
            self.open_path(path)

    def open_path(self, path):
        try:
            with open(path, encoding='utf-8') as f:
                text = f.read()
        except Exception as e:
            QMessageBox.critical(self, "错误", f"无法读取文件：{e}")
            return
        # .mdx 需剥离 JSX / import-export / 表达式后再走 markdown 流水线，
        # 避免朗读出一堆标签名；.md 直接处理
        ext = os.path.splitext(path)[1].lower()
        if ext == '.mdx':
            text = preprocess_mdx(text)
        self.units = parse_markdown(text)
        md_dir = os.path.dirname(os.path.abspath(path))
        self._md_text = text
        self._md_dir = md_dir
        self._render_view()
        self.lbl_file.setText(f"📄 {os.path.basename(path)}")
        self.lbl_count.setText(f"共 {len(self.units)} 句")
        self.current_idx = -1
        self.player.stop()
        self._reset_buttons()

    def _render_view(self):
        if not self._md_text:
            return
        self.view.set_content(
            render_markdown(parse_to_blocks(self._md_text), self._md_dir),
            self._md_dir,
        )

    # ---------------- 播放控制 ----------------
    def _cur_voice(self):
        return self.cb_voice.currentData()

    def _pct_to_wpm(self, pct):
        return max(80, min(320, round(180 * (1 + pct / 100.0))))

    def _start_playback(self, start_index):
        if not self.units:
            QMessageBox.information(self, "提示", "请先加载 MD 文件")
            return
        cur = self._cur_voice()
        if not cur:
            QMessageBox.warning(self, "提示", "请选择具体音色（在线或本地）")
            return
        engine, voice = cur['engine'], cur['id']
        pct = self.slider_rate.value()
        rate = pct if engine == 'online' else self._pct_to_wpm(pct)

        self.player.start(engine, self.units, voice, rate, start_index,
                         on_current=self._on_current, on_status=self._on_status,
                         on_finished=self._on_finished, on_error=self._on_error)
        self.paused = False
        self.btn_play.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.btn_pause.setEnabled(True)  # 在线 / 本地模式均支持暂停
        self.btn_pause.setText("⏸ 暂停")
        if engine == 'local':
            self.lbl_status.setText("本地模式：语速/音色下一句生效")
        else:
            self.lbl_status.setText("就绪")

    def on_play_from_start(self):
        self._start_playback(0)

    def on_sentence_clicked(self, idx):
        self._start_playback(idx)

    def on_pause_resume(self):
        if self.paused:
            self.player.resume()
            self.paused = False
            self.btn_pause.setText("⏸ 暂停")
        else:
            self.player.pause()
            self.paused = True
            self.btn_pause.setText("▶ 继续")

    def on_stop(self):
        self.player.stop()
        self._reset_buttons()
        self.view.clear_highlight()
        self.lbl_status.setText("已停止")

    def on_param_changed(self):
        if self.player.worker is None:
            return
        cur = self._cur_voice()
        if not cur:
            return
        if cur['engine'] == 'online':
            self.player.set_params(voice=cur['id'], rate_pct=self.slider_rate.value())
        else:
            self.player.set_params(voice=cur['id'], rate_wpm=self._pct_to_wpm(self.slider_rate.value()))

    def on_rate_changed(self, v):
        self.lbl_rate.setText(f"{v:+d}%")
        if self.player.worker is None:
            return
        cur = self._cur_voice()
        if not cur:
            return
        if cur['engine'] == 'online':
            self.player.set_params(rate_pct=v)
        else:
            self.player.set_params(rate_wpm=self._pct_to_wpm(v))

    # ---------------- 信号回调 ----------------
    def _on_current(self, idx):
        self.current_idx = idx
        self.view.highlight(idx)

    def _on_status(self, text):
        self.lbl_status.setText(text)

    def _on_finished(self):
        self._reset_buttons()
        self.view.clear_highlight()
        self.lbl_status.setText("朗读完成 ✓")

    def _on_error(self, msg):
        self._reset_buttons()
        QMessageBox.warning(self, "播放错误", msg)
        self.lbl_status.setText("出错")

    # ---------------- 工具 ----------------
    def _reset_buttons(self):
        self.btn_play.setEnabled(True)
        self.btn_pause.setEnabled(False)
        self.btn_pause.setText("⏸ 暂停")
        self.btn_stop.setEnabled(False)
        self.paused = False

    def closeEvent(self, event):
        self.player.stop()
        event.accept()


def main():
    # QtWebEngine 官方建议：创建 QApplication 前设置此属性，避免 OpenGL 上下文问题
    QApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)
    app = QApplication(sys.argv)
    # 设置应用名/组织名，使 QtWebEngine 缓存目录规范化（避免用脚本名当目录）
    app.setApplicationName("MDReader")
    app.setOrganizationName("MDReader")
    app.setFont(QFont(_UI_FONT, 10))
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
