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

import edge_tts
from PyQt5.QtCore import QThread, pyqtSignal


# 在线音色（edge-tts）
ONLINE_VOICES = [
    ("晓晓 (女·自然)", "zh-CN-XiaoxiaoNeural"),
    ("云希 (男·自然)", "zh-CN-YunxiNeural"),
    ("云扬 (男·专业)", "zh-CN-YunyangNeural"),
    ("晓伊 (女·温柔)", "zh-CN-XiaoyiNeural"),
    ("云健 (男)", "zh-CN-YunjianNeural"),
    ("辽宁-小贝 (女·方言)", "zh-CN-liaoning-XiaobeiNeural"),
    ("陕西-小妮 (女·方言)", "zh-CN-shaanxi-XiaoniNeural"),
    ("香港-晓曼 (女·粤语)", "zh-HK-HiuMaanNeural"),
    ("台湾-晓臻 (女·台语)", "zh-TW-HsiaoChenNeural"),
    ("台湾-云龙 (男·台语)", "zh-TW-YunlongNeural"),
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
    """edge-tts 在线合成 + pygame 播放，支持暂停/继续/实时换音色语速。"""

    def __init__(self, units, voice, rate_pct, start_index=0):
        super().__init__(units, start_index)
        self.voice = voice
        self.rate_pct = rate_pct
        self._cmd = None
        self._loop = None

    def pause(self):
        self._cmd = ('pause',)

    def resume(self):
        self._cmd = ('resume',)

    def set_params(self, voice=None, rate_pct=None):
        if voice is not None:
            self.voice = voice
        if rate_pct is not None:
            self.rate_pct = rate_pct
        self._cmd = ('params',)

    def _gen_audio(self, text):
        rate = f"{self.rate_pct:+d}%" if self.rate_pct else "+0%"
        comm = edge_tts.Communicate(text, self.voice, rate=rate)
        buf = io.BytesIO()

        async def _run():
            async for chunk in comm.stream():
                if chunk["type"] == "audio":
                    buf.write(chunk["data"])

        self._loop.run_until_complete(_run())
        buf.seek(0)
        return buf.read()

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
        elif kind == 'params':
            pass
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

        idx = self.start_index
        total = len(self.units)
        self.sig_status.emit("播放中")
        while idx < total:
            if self._stop:
                pygame.mixer.music.stop()
                return
            r = self._handle_cmd()
            if r == 'stop':
                pygame.mixer.music.stop()
                return
            self.sig_current.emit(idx)
            self.sig_status.emit(f"播放中 {idx+1}/{total}")
            text = self.units[idx]['text']
            try:
                audio = self._gen_audio(text)
            except Exception as e:
                self.sig_error.emit(f"第{idx+1}句生成失败：{e}")
                return
            r = self._handle_cmd()
            if r == 'stop':
                pygame.mixer.music.stop()
                return
            try:
                pygame.mixer.music.load(io.BytesIO(audio))
                pygame.mixer.music.play()
                while pygame.mixer.music.get_busy():
                    r = self._handle_cmd()
                    if r == 'stop':
                        pygame.mixer.music.stop()
                        return
                    self.msleep(50)
            except Exception as e:
                self.sig_error.emit(f"播放失败：{e}")
                return
            idx += 1

        self.sig_status.emit("朗读完成")
        self.sig_finished.emit()


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

    def set_params(self, voice=None, rate_wpm=None):
        if voice is not None:
            self.voice_id = voice
        if rate_wpm is not None:
            self.rate_wpm = rate_wpm

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
                # 等待本句播完，或中途停止
                while True:
                    if self._stop:
                        voice.Speak("", self.SPF_PURGE)
                        break
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
