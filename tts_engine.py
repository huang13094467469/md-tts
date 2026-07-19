# -*- coding: utf-8 -*-
"""TTS 播放引擎。
支持两种模式：
  - online : edge-tts 在线合成（音质高、需联网、有延迟）
  - local  : 本机 Windows SAPI 语音（离线、零延迟、最连贯）

本地模式直接用 comtypes 调用 Windows SAPI.SpVoice，绕开 pyttsx3 在
循环里多次 runAndWait 只朗读首句的已知问题，句间连续、可控、可中途停止。

两种 Worker 暴露相同的信号与停止接口，由 TTSPlayer 统一调度。
"""
import asyncio
import io
import threading
import time

import edge_tts
from PyQt5.QtCore import QThread, pyqtSignal


# 在线音色（edge-tts）—— 仅保留晓晓一个
ONLINE_VOICES = [
    ("晓晓 (在线·女声)", "zh-CN-XiaoxiaoNeural"),
]


def get_local_voices():
    """返回本机 SAPI 语音列表 [(显示名, voice_id), ...]，无则空。"""
    try:
        import comtypes.client
        v = comtypes.client.CreateObject("SAPI.SpVoice")
        toks = v.GetVoices()
        out = []
        for i in range(toks.Count):
            t = toks.Item(i)
            out.append((t.GetDescription(), t.Id))
        return out
    except Exception:
        return []


def get_local_chinese_voice():
    """返回本机「中文」SAPI 语音 (name, id)，无则取第一个本地语音，都没有则 None。

    用于把本地选项收敛为唯一一个中文系统语音，避免下拉列出一堆无关语音。
    """
    voices = get_local_voices()
    if not voices:
        return None
    # 优先命中中文语音
    for name, vid in voices:
        s = (name + vid).lower()
        if 'zh' in s or 'chs' in s or '中文' in s or 'chinese' in s:
            return (name, vid)
    # 没有显式中文标记时，退而取第一个可用语音
    return voices[0]


class BaseWorker(QThread):
    sig_current = pyqtSignal(int)
    sig_status = pyqtSignal(str)
    sig_finished = pyqtSignal()
    sig_error = pyqtSignal(str)

    def __init__(self, units, start_index=0):
        super().__init__()
        self.units = units
        self.start_index = start_index
        self._stop = False

    def stop(self):
        self._stop = True


class OnlineWorker(BaseWorker):
    """edge-tts 在线合成 + pygame 播放。

    关键能力：**预合成（预取）**。后台线程始终提前合成当前句之后约 LOOKAHEAD
    句的音频并缓存，主播放循环优先从缓存取，从而把 edge-tts 的网络延迟隐藏在
    播放过程中，句间几乎无停顿、连贯性大幅提升。同时支持暂停/继续、实时换音色语速。
    """

    LOOKAHEAD = 10  # 预合成窗口：保持当前句前方约 10 句已就绪
    PREFETCH_WAIT = 4.0  # 主循环等预合成的最长秒数，超时即降级同步合成
    SYNTH_TIMEOUT = 25.0  # 单句 edge-tts 合成的超时秒数，防止网络挂起卡死线程

    def __init__(self, units, voice, rate_pct, start_index=0):
        super().__init__(units, start_index)
        self.voice = voice
        self.rate_pct = rate_pct
        self._cmd = None
        self._loop = None
        # 预合成缓冲（受 self._cond 保护）
        self._cond = threading.Condition()
        self._prepared = {}            # idx -> (voice, rate_pct, audio_bytes)
        self._prep_errors = {}         # idx -> errmsg
        self._prep_idx = start_index  # 预合成游标（下一个要合成的句下标）
        self._play_idx = start_index - 1
        self._prep_finished = False
        self._prep_thread = None

    def pause(self):
        self._cmd = ('pause',)

    def resume(self):
        self._cmd = ('resume',)

    def set_params(self, voice=None, rate_pct=None):
        # 直接更新；预合成线程读最新值，旧缓存因参数不符在播放时被即时重合成
        if voice is not None:
            self.voice = voice
        if rate_pct is not None:
            self.rate_pct = rate_pct
        with self._cond:
            self._cond.notify_all()

    def _gen_audio(self, loop, text, voice=None, rate_pct=None):
        voice = self.voice if voice is None else voice
        rate_pct = self.rate_pct if rate_pct is None else rate_pct
        rate = f"{rate_pct:+d}%" if rate_pct else "+0%"
        comm = edge_tts.Communicate(text, voice, rate=rate)
        buf = io.BytesIO()

        async def _run():
            agen = comm.stream()
            try:
                while True:
                    # 逐块加超时，避免连接/传输挂起导致整个预合成线程永久卡死
                    chunk = await asyncio.wait_for(agen.__anext__(),
                                                   timeout=self.SYNTH_TIMEOUT)
                    if chunk["type"] == "audio":
                        buf.write(chunk["data"])
            except StopAsyncIteration:
                pass

        loop.run_until_complete(_run())
        buf.seek(0)
        return buf.read()

    # ---------------- 预合成后台线程 ----------------
    def _prep_loop(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            while not self._stop:
                with self._cond:
                    if self._prep_idx >= len(self.units):
                        self._prep_finished = True
                        self._cond.notify_all()
                        break
                    # 领先播放位置过多时，让出 CPU 等主循环推进
                    if self._prep_idx > self._play_idx + self.LOOKAHEAD:
                        ready = False
                    else:
                        i = self._prep_idx
                        self._prep_idx += 1
                        ready = True
                if not ready:
                    time.sleep(0.05)
                    continue
                # 单句合成失败（含超时）：记录错误后继续下一句，
                # 不允许单句卡死/崩溃整条预合成线程
                try:
                    audio = self._gen_audio(loop, self.units[i]['text'])
                except Exception as e:
                    with self._cond:
                        self._prep_errors[i] = str(e)
                        self._cond.notify_all()
                    continue
                with self._cond:
                    self._prepared[i] = (self.voice, self.rate_pct, audio)
                    self._cond.notify_all()
        finally:
            with self._cond:
                self._prep_finished = True
                self._cond.notify_all()

    def _take_audio(self, idx, text):
        """取第 idx 句音频。

        策略：优先用参数匹配的预合成缓存（零等待）；未命中则限时等待预合成线程
        PREFETCH_WAIT 秒；超时或预合成失败则用主线程同步兜底合成。这样即使预
        合成线程因网络卡在某句，主循环最多停顿 PREFETCH_WAIT 秒也会自行续上，
        绝不永久卡死（修复「前 10 句顺畅、后续读不动」的问题）。
        """
        # 1. 立即可用缓存
        with self._cond:
            item = self._prepared.get(idx)
            if item and item[0] == self.voice and item[1] == self.rate_pct:
                del self._prepared[idx]
                return item[2]
            if item:
                # 参数不符（用户中途切换音色/语速），丢弃旧缓存，等重写
                del self._prepared[idx]

        # 2. 限时等待预合成线程
        deadline = time.time() + self.PREFETCH_WAIT
        while time.time() < deadline:
            with self._cond:
                item = self._prepared.get(idx)
                if item and item[0] == self.voice and item[1] == self.rate_pct:
                    del self._prepared[idx]
                    return item[2]
                if idx in self._prep_errors:
                    # 预合成已尝试该句但失败 -> 跳出，走主线程兜底合成
                    self._prep_errors.pop(idx)
                    break
                if self._prep_finished and self._prep_idx > idx:
                    # 预合成已越过该句却仍无缓存（异常）-> 兜底
                    break
            time.sleep(0.05)

        # 3. 兜底：主线程自己合成（同样带超时保护）
        try:
            return self._gen_audio(self._loop, text)
        except Exception as e:
            raise Exception(f"第{idx+1}句生成失败：{e}")

    def _handle_cmd(self):
        cmd = self._cmd
        self._cmd = None
        if cmd is None:
            return None
        kind = cmd[0]
        if kind == 'stop':
            return 'stop'
        elif kind == 'pause':
            import pygame
            pygame.mixer.music.pause()
            self.sig_status.emit("已暂停")
            while True:
                c = self._cmd
                if c is None:
                    self.msleep(80)
                    continue
                k = c[0]
                self._cmd = None
                if k == 'resume':
                    pygame.mixer.music.unpause()
                    self.sig_status.emit("播放中")
                    return None
                elif k == 'stop':
                    pygame.mixer.music.stop()
                    return 'stop'
        elif kind == 'resume':
            import pygame
            pygame.mixer.music.unpause()
            self.sig_status.emit("播放中")
        return None

    def run(self):
        import pygame
        try:
            pygame.mixer.pre_init(frequency=24000, size=-16, channels=1, buffer=4096)
            pygame.mixer.init()
            try:
                pygame.display.set_mode((1, 1))
            except Exception:
                pass
        except Exception as e:
            self.sig_error.emit(f"音频初始化失败：{e}")
            return

        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        # 启动预合成后台线程（daemon，主线程退出即终止）
        self._prep_thread = threading.Thread(target=self._prep_loop, daemon=True)
        self._prep_thread.start()

        idx = self.start_index
        total = len(self.units)
        self.sig_status.emit("播放中")
        while idx < total:
            if self._stop:
                pygame.mixer.music.stop()
                self._stop = True
                with self._cond:
                    self._cond.notify_all()
                return
            r = self._handle_cmd()
            if r == 'stop':
                pygame.mixer.music.stop()
                self._stop = True
                with self._cond:
                    self._cond.notify_all()
                return
            with self._cond:
                self._play_idx = idx
            self.sig_current.emit(idx)
            self.sig_status.emit(f"播放中 {idx+1}/{total}")
            text = self.units[idx]['text']
            try:
                audio = self._take_audio(idx, text)
            except Exception as e:
                self.sig_error.emit(f"第{idx+1}句生成失败：{e}")
                self._stop = True
                with self._cond:
                    self._cond.notify_all()
                return
            r = self._handle_cmd()
            if r == 'stop':
                pygame.mixer.music.stop()
                self._stop = True
                with self._cond:
                    self._cond.notify_all()
                return
            try:
                pygame.mixer.music.load(io.BytesIO(audio))
                pygame.mixer.music.play()
                while pygame.mixer.music.get_busy():
                    r = self._handle_cmd()
                    if r == 'stop':
                        pygame.mixer.music.stop()
                        self._stop = True
                        with self._cond:
                            self._cond.notify_all()
                        return
                    self.msleep(50)
            except Exception as e:
                self.sig_error.emit(f"播放失败：{e}")
                self._stop = True
                with self._cond:
                    self._cond.notify_all()
                return
            idx += 1

        self.sig_status.emit("朗读完成")
        self.sig_finished.emit()
        # 收尾：通知预合成线程退出并等待
        self._stop = True
        with self._cond:
            self._cond.notify_all()
        try:
            self._prep_thread.join(timeout=3)
        except Exception:
            pass


class LocalWorker(BaseWorker):
    """本机 SAPI 语音（离线、零延迟、句间连续）。

    直接用 comtypes 调用 Windows SAPI.SpVoice：
      - 逐句 Speak(异步) + WaitUntilDone 轮询，保证每句都真正朗读；
      - 句间连续、无重新初始化延迟；
      - 支持播放中实时切换音色/语速（下一句生效）；
      - 停止时清除队列并终止当前句。
    """

    # SAPI Speak 标志
    SPF_ASYNC = 1
    SPF_PURGE = 2

    def __init__(self, units, voice_id, rate_wpm, start_index=0):
        super().__init__(units, start_index)
        self.voice_id = voice_id
        self.rate_wpm = rate_wpm
        self._paused = False

    def set_params(self, voice=None, rate_wpm=None):
        if voice is not None:
            self.voice_id = voice
        if rate_wpm is not None:
            self.rate_wpm = rate_wpm

    def pause(self):
        # 仅置标志，真正的 SAPI Pause 在 run() 等待循环里执行（避免跨线程调 COM）
        self._paused = True

    def resume(self):
        self._paused = False

    @staticmethod
    def _wpm_to_sapi_rate(wpm):
        # SAPI Rate 范围 -10..10，0≈默认(约 180 字/分)
        r = round((wpm - 180) / 10.0)
        return max(-10, min(10, r))

    def _apply_voice(self, voice):
        if not self.voice_id:
            return
        try:
            toks = voice.GetVoices()
            for i in range(toks.Count):
                t = toks.Item(i)
                if t.Id == self.voice_id:
                    voice.Voice = t
                    return
        except Exception:
            pass

    def run(self):
        try:
            import comtypes.client
        except Exception as e:
            self.sig_error.emit(f"本地语音组件缺失（需 comtypes）：{e}")
            return

        try:
            voice = comtypes.client.CreateObject("SAPI.SpVoice")
        except Exception as e:
            self.sig_error.emit(f"本地语音初始化失败（系统可能无语音包）：{e}")
            return

        # 先应用一次音色
        self._apply_voice(voice)

        idx = self.start_index
        total = len(self.units)
        self.sig_status.emit("播放中（本地）")
        while idx < total:
            if self._stop:
                try:
                    voice.Speak("", self.SPF_PURGE)
                except Exception:
                    pass
                self.sig_status.emit("已停止")
                break

            # 暂停态下等待恢复，再朗读下一句
            while self._paused and not self._stop:
                self.msleep(80)
            if self._stop:
                self.sig_status.emit("已停止")
                break

            # 逐句应用最新语速/音色（实时切换下一句生效）
            try:
                voice.Rate = self._wpm_to_sapi_rate(self.rate_wpm)
            except Exception:
                pass
            self._apply_voice(voice)

            text = self.units[idx]['text']
            if not text.strip():
                idx += 1
                continue

            self.sig_current.emit(idx)
            self.sig_status.emit(f"播放中（本地）{idx+1}/{total}")

            try:
                voice.Speak(text, self.SPF_ASYNC)
                # 等待本句播完；支持中途暂停（SAPI Pause）与停止
                paused = False
                while True:
                    if self._stop:
                        voice.Speak("", self.SPF_PURGE)
                        break
                    if self._paused and not paused:
                        try:
                            voice.Pause()
                        except Exception:
                            pass
                        paused = True
                        self.sig_status.emit("已暂停（本地）")
                    elif not self._paused and paused:
                        try:
                            voice.Resume()
                        except Exception:
                            pass
                        paused = False
                        self.sig_status.emit(f"播放中（本地）{idx+1}/{total}")
                    if voice.WaitUntilDone(50):
                        break
            except Exception as e:
                self.sig_error.emit(f"本地朗读失败（系统音频或语音包问题）：{e}")
                break

            if self._stop:
                self.sig_status.emit("已停止")
                break
            idx += 1

        try:
            # 释放 SAPI 对象
            del voice
        except Exception:
            pass
        if not self._stop:
            self.sig_status.emit("朗读完成")
        self.sig_finished.emit()


class TTSPlayer:
    """对 GUI 友好的播放控制器包装。"""

    def __init__(self):
        self.worker = None
        self.engine_type = None

    def start(self, engine_type, units, voice, rate, start_index=0,
              on_current=None, on_status=None, on_finished=None, on_error=None):
        self.stop()
        if engine_type == 'online':
            w = OnlineWorker(units, voice, rate, start_index)
        else:
            w = LocalWorker(units, voice, rate, start_index)
        if on_current:  w.sig_current.connect(on_current)
        if on_status:   w.sig_status.connect(on_status)
        if on_finished: w.sig_finished.connect(on_finished)
        if on_error:    w.sig_error.connect(on_error)
        self.worker = w
        self.engine_type = engine_type
        w.start()

    def pause(self):
        if self.worker and hasattr(self.worker, 'pause'):
            self.worker.pause()

    def resume(self):
        if self.worker and hasattr(self.worker, 'resume'):
            self.worker.resume()

    def set_params(self, **kw):
        if self.worker and hasattr(self.worker, 'set_params'):
            self.worker.set_params(**kw)

    def stop(self):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait(1500)
            try:
                self.worker.terminate()
            except Exception:
                pass
        self.worker = None
        self.engine_type = None
