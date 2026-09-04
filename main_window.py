# -*- coding: utf-8 -*-
"""MD 朗读工具主界面：加载 MD 目录 -> 树形预览 -> 点击句子定位朗读 -> 在线/本地双引擎。

预览区使用 QWebEngineView（Chromium 内核），标准 markdown 渲染、图片自适应不跳动；
点击句子通过自定义 mdtts:N 协议回传 Python，当前句高亮由前端 JS 完成。
"""
import os
import sys
import threading

# 必须在 import QtWebEngineWidgets 之前设置，否则 Chromium 子进程在受限/
# 沙箱环境下启动失败会导致预览区白屏（Windows 上最常见根因；Mac 上一般无害）
os.environ.setdefault('QTWEBENGINE_DISABLE_SANDBOX', '1')

# 平台感知字体：Mac 用系统原生字体，Windows 保持原配置
IS_MAC = sys.platform == 'darwin'
_UI_FONT = 'PingFang SC' if IS_MAC else 'Microsoft YaHei'

from PyQt5.QtCore import Qt, QTimer, QSettings
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QComboBox, QSlider, QLabel, QFileDialog, QMessageBox, QAction, QSplitter,
    QTreeWidget, QTreeWidgetItem, QFrame, QPushButton, QInputDialog,
    QCheckBox, QDialog, QVBoxLayout as QVBoxLayout2,
    QListWidget as QListWidget2, QListWidgetItem, QPushButton as QPushButton2,
)
from PyQt5.QtGui import QFont, QKeySequence

from md_parser import parse_markdown, parse_to_blocks, preprocess_mdx
from markdown_render import render_markdown
from markdown_view import MarkdownView
from log_panel import LogPanel, get_gui_log_handler, _log_bridge
from tts_engine import TTSPlayer, get_local_chinese_voice
from app_logger import logger


# 左侧树中用于标记「文档叶子节点」的 data role（目录节点不设此标记）
_FILE_ROLE = Qt.UserRole + 1


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.units = []
        self.player = TTSPlayer()
        self.current_idx = -1
        self.paused = False
        self._md_text = ''
        self._md_dir = ''
        self._current_path = ''  # 当前文档绝对路径，用于 TTS 缓存与连续阅读跳转
        self._user_stopped = False  # 用户主动停止标志，避免自然读完时误触发跳转
        self._doc_order = []  # 打开目录后树中所有文档的阅读顺序（绝对路径列表）
        self._init_ui()
        self._init_voices()
        self._setup_gui_log()

    # ---------------- GUI 日志接管 ----------------
    def _setup_gui_log(self):
        """把全局 logger 接到 GUI 日志面板（跨线程安全）。"""
        _log_bridge.newLog.connect(self.log_panel.append_log)
        logger.addHandler(get_gui_log_handler())

    # ---------------- UI ----------------
    def _init_ui(self):
        self.setWindowTitle("MD 朗读工具 · TTS Reader")
        self.resize(1200, 780)

        menubar = self.menuBar()
        file_menu = menubar.addMenu("文件")
        open_dir_act = file_menu.addAction("打开 MD/MDX 目录")
        open_dir_act.triggered.connect(self.open_dir)
        file_menu.addSeparator()
        quit_act = QAction("退出", self)
        quit_act.setShortcut(QKeySequence.Quit)  # Mac: Cmd+Q, Win: Ctrl+Q
        quit_act.triggered.connect(self.close)
        file_menu.addAction(quit_act)
        dir_menu = menubar.addMenu("示例")
        dir_menu.addAction("加载工作区 MD 列表").triggered.connect(self.load_workspace_md)
        tool_menu = menubar.addMenu("工具")
        tool_menu.addAction("清理 TTS 缓存…").triggered.connect(self.clear_tts_cache)

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

        # 左右分栏：左侧树形文档列表，右侧 Markdown 渲染预览
        splitter = QSplitter(Qt.Horizontal)
        self.doc_tree = QTreeWidget()
        self.doc_tree.setHeaderHidden(True)
        self.doc_tree.setFixedWidth(260)
        self.doc_tree.setStyleSheet(
            "QTreeWidget{font-size:13px;} "
            "QTreeWidget::item{padding:4px 6px;} "
            "QTreeWidget::item:selected{background:#ddf4ff;color:#0969da;}")
        self.doc_tree.itemClicked.connect(self.on_doc_selected)
        splitter.addWidget(self.doc_tree)

        self.view = MarkdownView()
        self.view.sentenceClicked.connect(self.on_sentence_clicked)
        splitter.addWidget(self.view)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([260, 940])
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
        row2.addSpacing(16)
        self.chk_continuous = QCheckBox("连续阅读")
        self.chk_continuous.setToolTip(
            "勾选：读完当前文档自动读下一个，读完整个列表后从头循环\n"
            "未勾选：读完当前文档后从该文档开头重新循环")
        row2.addWidget(self.chk_continuous)
        row2.addStretch(1)
        self.lbl_status = QLabel("就绪")
        self.lbl_status.setStyleSheet("color:#666;")
        row2.addWidget(self.lbl_status)
        cl.addLayout(row2)

        root.addWidget(ctrl)

        # 可折叠运行日志面板（折叠/清空/限行逻辑封装在 LogPanel 中）
        self.log_panel = LogPanel()
        root.addWidget(self.log_panel)

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
    def _last_dir(self):
        """读取上次打开的目录，不存在则返回用户主目录。"""
        d = QSettings().value('lastOpenDir', '', type=str)
        if d and os.path.isdir(d):
            return d
        return os.path.expanduser('~')

    def _save_last_dir(self, path):
        """保存上次打开的目录（取文件或目录的父目录）。"""
        if path:
            d = path if os.path.isdir(path) else os.path.dirname(path)
            if d and os.path.isdir(d):
                QSettings().setValue('lastOpenDir', d)

    def on_doc_selected(self, item):
        """点击左侧树形列表项：若是文档叶子，渲染对应文档。
        （目录节点仅用于展开，点击不加载。）
        """
        if item is None:
            return
        if not item.data(0, _FILE_ROLE):  # 目录节点（未标记为文件）
            return
        path = item.data(0, Qt.UserRole)
        if path:
            logger.info("选择文档: %s", path)
            self.open_path(path)

    def open_dir(self):
        """选择一个目录，按其子目录结构构建左侧树；文档叶子按目录分组的顺序排列。"""
        path = QFileDialog.getExistingDirectory(self, "打开 Markdown 目录", self._last_dir())
        if not path:
            return
        self._save_last_dir(path)
        logger.info("打开目录: %s", path)
        self.doc_tree.clear()
        self._doc_order = []
        abspath = os.path.abspath(path)
        root_item = QTreeWidgetItem([os.path.basename(abspath) or abspath])
        self.doc_tree.addTopLevelItem(root_item)
        root_item.setExpanded(True)
        n = self._populate_tree(root_item, abspath)
        if n == 0:
            logger.warning("目录下未找到 .md/.mdx: %s", path)
            self.doc_tree.clear()
            QMessageBox.information(self, "提示", "该目录下未找到 .md / .mdx 文件")
            return
        logger.info("扫描到 %d 个文档", n)
        # 默认选中第一个文档并渲染
        if self._doc_order:
            self._select_tree_node(self._doc_order[0])
            self.open_path(self._doc_order[0])

    def _populate_tree(self, node, abspath):
        """递归把 abspath 下的子目录与 .md/.mdx 文档填入 node，返回文档计数。

        目录在前、文件在后，各自按名称排序；空的子目录会被移除。树的前序遍历
        顺序同时写入 self._doc_order，作为连续阅读的阅读顺序。
        """
        count = 0
        try:
            entries = sorted(os.scandir(abspath),
                             key=lambda e: (not e.is_dir(), e.name.lower()))
        except OSError:
            return 0
        # 用系统风格内置图标区分文件夹与文件（QStyle 提供，无需打包额外资源）
        style = QApplication.style()
        io_dir = style.standardIcon(style.SP_DirIcon)
        io_file = style.standardIcon(style.SP_FileIcon)
        for e in entries:
            if e.name.startswith('.'):
                continue
            full = os.path.abspath(e.path)
            if e.is_dir():
                sub = QTreeWidgetItem([e.name])
                sub.setIcon(0, io_dir)  # 文件夹：专用图标
                sub.setData(0, Qt.UserRole, full)
                node.addChild(sub)
                c = self._populate_tree(sub, full)
                if c == 0:
                    node.removeChild(sub)  # 无文档的空目录，不展示
                else:
                    count += c
            elif e.name.lower().endswith(('.md', '.mdx')):
                # 文件节点显示完整文件名（含扩展名），与文件夹在名称与图标上区分
                it = QTreeWidgetItem([e.name])
                it.setIcon(0, io_file)  # 文件：专用图标
                it.setData(0, Qt.UserRole, full)
                it.setData(0, _FILE_ROLE, True)  # 标记为可加载的文档叶子
                it.setToolTip(0, full)
                node.addChild(it)
                self._doc_order.append(full)
                count += 1
        return count

    def _select_tree_node(self, path):
        """在树中定位节点并选中，同时展开到其祖先目录链、滚动到可见。"""
        stack = [self.doc_tree.topLevelItem(i)
                 for i in range(self.doc_tree.topLevelItemCount())]
        while stack:
            it = stack.pop()
            if it.data(0, Qt.UserRole) == path:
                self.doc_tree.blockSignals(True)
                self.doc_tree.setCurrentItem(it)
                self.doc_tree.blockSignals(False)
                p = it.parent()
                while p:
                    p.setExpanded(True)
                    p = p.parent()
                self.doc_tree.scrollToItem(it)
                return
            for c in range(it.childCount()):
                stack.append(it.child(c))

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

    def open_path(self, path):
        logger.info("加载文件: %s", path)
        try:
            with open(path, encoding='utf-8') as f:
                text = f.read()
        except Exception as e:
            logger.exception("文件读取失败: %s", path)
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
        self._current_path = os.path.abspath(path)
        self._render_view()
        self.lbl_file.setText(f"📄 {os.path.basename(path)}")
        self.lbl_count.setText(f"共 {len(self.units)} 句")
        self.current_idx = -1
        self.player.stop()
        self._reset_buttons()
        # 同步左侧树选中状态（_select_tree_node 内部已 blockSignals，不会触发重载）
        self._select_tree_node(os.path.abspath(path))

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
        self._user_stopped = False
        engine, voice = cur['engine'], cur['id']
        pct = self.slider_rate.value()
        rate = pct if engine == 'online' else self._pct_to_wpm(pct)
        logger.info("开始播放: engine=%s voice=%s rate=%s 句数=%d 起始=%d",
                    engine, voice, rate, len(self.units), start_index)

        self.player.start(engine, self.units, voice, rate, start_index,
                         on_current=self._on_current, on_status=self._on_status,
                         on_finished=self._on_finished, on_error=self._on_error,
                         source_path=self._current_path)
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
        self._user_stopped = True
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
        logger.info("朗读完成")
        self.view.clear_highlight()
        # 用户主动停止：不触发跳转/循环
        if self._user_stopped:
            self._reset_buttons()
            self.lbl_status.setText("已停止")
            return
        # 连续阅读：按树的前序遍历顺序跳到下一篇（读到最后回到第一篇，循环）
        if self.chk_continuous.isChecked() and len(self._doc_order) > 1:
            cur_full = (self._current_path or '')
            try:
                cur = self._doc_order.index(cur_full)
            except ValueError:
                cur = -1
            next_path = self._doc_order[(cur + 1) % len(self._doc_order)] if cur >= 0 \
                else self._doc_order[0]
            logger.info("连续阅读: %s -> %s", cur_full, next_path)
            self.lbl_status.setText("切换到下一篇…")
            QTimer.singleShot(800, lambda: self._play_doc_at_path(next_path))
            return
        # 未勾连续阅读：单文件循环，从当前文档开头重新开始
        if self.units:
            logger.info("单文件循环: 重新开始")
            self.lbl_status.setText("循环重读…")
            QTimer.singleShot(800, lambda: self._start_playback(0))
            return
        self._reset_buttons()
        self.lbl_status.setText("朗读完成 ✓")

    def _play_doc_at_path(self, path):
        """切换到指定路径的文档并从头播放（连续阅读跳转用）。"""
        if not path or not os.path.isfile(path):
            return
        # 切换文档（会重新渲染、加载 units）
        self.open_path(path)
        # 稍延迟启动播放，等文档渲染就绪
        QTimer.singleShot(300, lambda: self._start_playback(0))

    def _on_error(self, msg):
        logger.error("播放错误: %s", msg)
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

    def clear_tts_cache(self):
        """清理 TTS 缓存对话框：可按文件清理，或清空全部。"""
        import tts_cache
        st = tts_cache.stats()
        dlg = QDialog(self)
        dlg.setWindowTitle("清理 TTS 缓存")
        dlg.resize(520, 420)
        lay = QVBoxLayout2(dlg)

        info = QLabel(f"当前缓存：{st['count']} 条，共 {st['size_mb']} MB\n"
                      f"缓存目录：{tts_cache.cache_path()}")
        info.setWordWrap(True)
        info.setStyleSheet("color:#555; margin-bottom:8px;")
        lay.addWidget(info)

        lay.addWidget(QLabel("按文件清理（勾选要清理的文档）："))
        lst = QListWidget2()
        lst.setSelectionMode(QListWidget2.MultiSelection)
        # 列出当前打开目录下的全部文档
        for path in self._doc_order:
            display = f"{os.path.basename(path)}  ({path})"
            item = QListWidgetItem(display)
            item.setData(Qt.UserRole, path)
            lst.addItem(item)
        lay.addWidget(lst, 1)

        btns = QHBoxLayout()
        btn_clear_sel = QPushButton2("清理选中")
        btn_clear_all = QPushButton2("清空全部")
        btn_close = QPushButton2("关闭")
        btns.addWidget(btn_clear_sel)
        btns.addWidget(btn_clear_all)
        btns.addStretch(1)
        btns.addWidget(btn_close)
        lay.addLayout(btns)

        result = {'msg': ''}

        def do_clear_sel():
            paths = [lst.item(i).data(Qt.UserRole)
                     for i in range(lst.count()) if lst.item(i).isSelected()]
            if not paths:
                QMessageBox.information(dlg, "提示", "请先勾选要清理的文档")
                return
            total = 0
            for p in paths:
                total += tts_cache.clear_by_path(p)
            logger.info("按文件清理缓存: %d 个文件, 清除 %d 条", len(paths), total)
            QMessageBox.information(dlg, "完成", f"已清理 {total} 条缓存")
            dlg.accept()

        def do_clear_all():
            r = QMessageBox.question(dlg, "确认", "确认清空全部 TTS 缓存？此操作不可撤销。")
            if r != QMessageBox.Yes:
                return
            n = tts_cache.clear_all()
            logger.info("清空全部缓存: %d 条", n)
            QMessageBox.information(dlg, "完成", f"已清空 {n} 条缓存")
            dlg.accept()

        btn_clear_sel.clicked.connect(do_clear_sel)
        btn_clear_all.clicked.connect(do_clear_all)
        btn_close.clicked.connect(dlg.accept)
        dlg.exec_()

    def closeEvent(self, event):
        logger.info("窗口关闭，开始退出流程")
        # 非阻塞退出：把 worker 停止放到后台线程，主线程立即关窗口。
        # 用 force=False 不强杀线程，让 daemon 预合成线程随进程自然清理，
        # 避免 terminate() 在 Mac 上残留 AVSpeechSynthesizer / pygame 资源导致卡顿。
        worker = self.player.worker
        if worker and worker.isRunning():
            worker.stop()
            threading.Thread(
                target=lambda: (worker.wait(800), worker.terminate() if worker.isRunning() else None),
                daemon=True,
            ).start()
        self.player.worker = None
        event.accept()


def main():
    import platform
    logger.info("=" * 50)
    logger.info("MDReader 启动 | Python %s | %s | %s",
                platform.python_version(), platform.system(), platform.machine())
    logger.info("日志文件: %s", __import__('app_logger').log_path())
    # QtWebEngine 官方建议：创建 QApplication 前设置此属性，避免 OpenGL 上下文问题
    QApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)
    app = QApplication(sys.argv)
    # 设置应用名/组织名，使 QtWebEngine 缓存目录规范化（避免用脚本名当目录）
    app.setApplicationName("MDReader")
    app.setOrganizationName("MDReader")
    app.setFont(QFont(_UI_FONT, 10))
    win = MainWindow()
    win.show()
    logger.info("主窗口已显示")
    exit_code = app.exec_()
    logger.info("应用退出，exit_code=%s", exit_code)
    sys.exit(exit_code)


if __name__ == '__main__':
    main()
