# -*- coding: utf-8 -*-
"""MD 朗读工具主界面：加载 MD -> 规范渲染预览 -> 点击句子定位朗读 -> 在线/本地双引擎。"""
import os
import sys

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTextBrowser, QComboBox, QSlider, QLabel, QFileDialog, QMessageBox,
    QSplitter, QPlainTextEdit, QFrame, QTextEdit, QPushButton,
)
from PyQt5.QtGui import QFont, QColor, QTextCursor

from md_parser import parse_markdown, parse_to_blocks
from markdown_render import render_markdown
from tts_engine import TTSPlayer, ONLINE_VOICES, get_local_voices


class MarkdownView(QTextBrowser):
    """规范 markdown 渲染视图，点击句子即定位朗读，支持当前句高亮。"""
    sentenceClicked = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setOpenLinks(False)
        self.setReadOnly(True)
        self.sentence_ranges = {}  # idx -> (start_char, end_char)

    def mousePressEvent(self, e):
        super().mousePressEvent(e)
        cur = self.cursorForPosition(e.pos())
        idx = None
        for n in cur.charFormat().anchorNames():
            if n.startswith('u'):
                try:
                    idx = int(n[1:])
                    break
                except ValueError:
                    pass
        if idx is None:
            idx = self._nearest(cur.position())
        if idx is not None:
            self.sentenceClicked.emit(idx)

    def _nearest(self, pos):
        if not self.sentence_ranges:
            return None
        best, bd = None, float('inf')
        for i, (s, en) in self.sentence_ranges.items():
            d = abs(((s + en) // 2) - pos)
            if d < bd:
                bd, best = d, i
        return best


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.units = []
        self.player = TTSPlayer()
        self.current_idx = -1
        self.paused = False
        self._init_ui()
        self._init_voices()

    # ---------------- UI ----------------
    def _init_ui(self):
        self.setWindowTitle("MD 朗读工具 · TTS Reader")
        self.resize(1200, 780)

        menubar = self.menuBar()
        file_menu = menubar.addMenu("文件")
        open_act = file_menu.addAction("打开 MD 文件")
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

        splitter = QSplitter(Qt.Horizontal)
        self.view = MarkdownView()
        self.view.sentenceClicked.connect(self.on_sentence_clicked)
        self.view.setFont(QFont("Microsoft YaHei", 11))
        splitter.addWidget(self.view)

        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setFont(QFont("Consolas", 10))
        splitter.addWidget(self.preview)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        root.addWidget(splitter, 1)

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
        self.slider_rate.setValue(0)
        self.slider_rate.setTickPosition(QSlider.TicksBelow)
        self.slider_rate.setFixedWidth(220)
        self.slider_rate.valueChanged.connect(self.on_rate_changed)
        row1.addWidget(self.slider_rate)
        self.lbl_rate = QLabel("+0%")
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
        self._hl_color = QColor("#FFF3B0")

    # ---------------- 音色列表 ----------------
    def _init_voices(self):
        self.cb_voice.clear()
        self.cb_voice.addItem("── 在线（需联网）──")
        self._disable_item(self.cb_voice.count() - 1)
        for label, vid in ONLINE_VOICES:
            self.cb_voice.addItem(label, {'engine': 'online', 'id': vid})

        locals_ = get_local_voices()
        self.cb_voice.addItem("── 本地系统（离线）──")
        self._disable_item(self.cb_voice.count() - 1)
        if locals_:
            for name, vid in locals_:
                self.cb_voice.addItem(name, {'engine': 'local', 'id': vid})
        else:
            self.cb_voice.addItem("（未检测到本地语音）")
            self._disable_item(self.cb_voice.count() - 1)

        self.cb_voice.setCurrentIndex(1)

    def _disable_item(self, idx):
        self.cb_voice.setItemData(idx, None)
        self.cb_voice.model().item(idx).setEnabled(False)

    # ---------------- 数据 ----------------
    def load_workspace_md(self):
        parent = os.path.dirname(os.path.abspath(__file__))
        parent = os.path.dirname(parent)
        md_files = [os.path.join(parent, n) for n in os.listdir(parent)
                    if n.lower().endswith('.md') and os.path.isfile(os.path.join(parent, n))]
        if not md_files:
            QMessageBox.information(self, "提示", "工作区未找到 .md 文件")
            return
        names = [os.path.basename(f) for f in md_files]
        from PyQt5.QtWidgets import QInputDialog
        name, ok = QInputDialog.getItem(self, "选择 MD 文件", "工作区文件:", names, 0, False)
        if ok:
            self.open_path(md_files[names.index(name)])

    def open_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "打开 Markdown 文件", "", "Markdown (*.md);;All (*.*)")
        if path:
            self.open_path(path)

    def open_path(self, path):
        try:
            with open(path, encoding='utf-8') as f:
                text = f.read()
        except Exception as e:
            QMessageBox.critical(self, "错误", f"无法读取文件：{e}")
            return
        self.units = parse_markdown(text)
        self.view.setHtml(render_markdown(parse_to_blocks(text)))
        self._build_ranges()
        self.lbl_file.setText(f"📄 {os.path.basename(path)}")
        self.lbl_count.setText(f"共 {len(self.units)} 句")
        self.preview.setPlainText(text)
        self.current_idx = -1
        self.player.stop()
        self._reset_buttons()

    def _build_ranges(self):
        self.view.sentence_ranges = {}
        doc = self.view.document()
        block = doc.begin()
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                f = it.fragment()
                for n in f.charFormat().anchorNames():
                    if n.startswith('u'):
                        try:
                            i = int(n[1:])
                            self.view.sentence_ranges[i] = (f.position(), f.position() + f.length())
                        except ValueError:
                            pass
                it += 1
            block = block.next()

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
        if engine == 'local':
            self.btn_pause.setEnabled(False)
            self.btn_pause.setText("⏸ 暂停(本地)")
            self.lbl_status.setText("本地模式：语速/音色下一句生效，停止在当前句后生效")
        else:
            self.btn_pause.setEnabled(True)
            self.btn_pause.setText("⏸ 暂停")
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
        self._clear_highlight()
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
        self._clear_highlight()
        rng = self.view.sentence_ranges.get(idx)
        if rng:
            s, e = rng
            cur = QTextCursor(self.view.document())
            cur.setPosition(s)
            cur.setPosition(e, QTextCursor.KeepAnchor)
            extra = QTextEdit.ExtraSelection()
            extra.cursor = cur
            extra.format.setBackground(self._hl_color)
            self.view.setExtraSelections([extra])
            self.view.setTextCursor(cur)

    def _on_status(self, text):
        self.lbl_status.setText(text)

    def _on_finished(self):
        self._reset_buttons()
        self._clear_highlight()
        self.lbl_status.setText("朗读完成 ✓")

    def _on_error(self, msg):
        self._reset_buttons()
        QMessageBox.warning(self, "播放错误", msg)
        self.lbl_status.setText("出错")

    # ---------------- 工具 ----------------
    def _clear_highlight(self):
        self.view.setExtraSelections([])

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
    app = QApplication(sys.argv)
    app.setFont(QFont("Microsoft YaHei", 10))
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
