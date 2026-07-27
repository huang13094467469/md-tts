# -*- coding: utf-8 -*-
"""TTS 音频磁盘缓存。

在线引擎 edge-tts 合成较慢且耗流量，循环阅读时重复合成浪费资源。
本模块按 (voice, rate, 文本) 哈希缓存合成结果到磁盘，未命中才合成。

缓存目录：~/Library/Caches/MDReader/tts/
索引文件：index.json 记录 {文件绝对路径: [缓存key列表]}，用于按文件清理。

缓存 key = sha256(voice + rate + text) 的前 32 字符，避免文件名过长。
"""
import hashlib
import json
import os
import sys
import threading

_APP_NAME = "MDReader"
_lock = threading.Lock()


def _cache_dir():
    if sys.platform == 'darwin':
        base = os.path.expanduser('~/Library/Caches')
    else:
        base = os.path.dirname(os.path.abspath(sys.executable))
    d = os.path.join(base, _APP_NAME, 'tts')
    os.makedirs(d, exist_ok=True)
    return d


def cache_path():
    """返回缓存目录路径。"""
    return _cache_dir()


def _index_file():
    return os.path.join(_cache_dir(), 'index.json')


def _load_index():
    """加载路径->key 索引。返回 dict。"""
    try:
        with open(_index_file(), 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _save_index(idx):
    try:
        with open(_index_file(), 'w', encoding='utf-8') as f:
            json.dump(idx, f, ensure_ascii=False)
    except Exception:
        pass


def _make_key(voice, rate, text):
    """生成缓存 key：sha256(voice|rate|text) 前 32 字符。"""
    raw = f"{voice}|{rate}|{text}"
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:32]


def _audio_file(key):
    return os.path.join(_cache_dir(), f"{key}.mp3")


def get(voice, rate, text):
    """查缓存。命中返回 bytes，未命中返回 None。"""
    key = _make_key(voice, rate, text)
    f = _audio_file(key)
    with _lock:
        if os.path.exists(f):
            try:
                with open(f, 'rb') as fp:
                    return fp.read()
            except Exception:
                return None
    return None


def put(voice, rate, text, audio, source_path=None):
    """写入缓存。source_path 若提供，会记录到索引以便按文件清理。"""
    key = _make_key(voice, rate, text)
    f = _audio_file(key)
    with _lock:
        try:
            with open(f, 'wb') as fp:
                fp.write(audio)
        except Exception:
            return
        # 更新索引：路径 -> key 列表
        if source_path:
            idx = _load_index()
            keys = set(idx.get(source_path, []))
            keys.add(key)
            idx[source_path] = sorted(keys)
            _save_index(idx)


def clear_by_path(path):
    """清理指定文件路径对应的所有缓存。返回清理的条目数。"""
    with _lock:
        idx = _load_index()
        keys = idx.pop(path, [])
        n = 0
        for k in keys:
            f = _audio_file(k)
            if os.path.exists(f):
                try:
                    os.remove(f)
                    n += 1
                except Exception:
                    pass
        _save_index(idx)
        return n


def clear_all():
    """清空全部缓存。返回清理的条目数。"""
    with _lock:
        d = _cache_dir()
        n = 0
        for name in os.listdir(d):
            if name.endswith('.mp3') or name == 'index.json':
                try:
                    os.remove(os.path.join(d, name))
                    if name.endswith('.mp3'):
                        n += 1
                except Exception:
                    pass
        return n


def stats():
    """返回缓存统计：条目数、总大小（MB）。"""
    with _lock:
        d = _cache_dir()
        n = 0
        size = 0
        for name in os.listdir(d):
            if name.endswith('.mp3'):
                n += 1
                try:
                    size += os.path.getsize(os.path.join(d, name))
                except Exception:
                    pass
        return {'count': n, 'size_mb': round(size / 1024 / 1024, 1)}
