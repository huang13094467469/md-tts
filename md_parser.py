# -*- coding: utf-8 -*-
"""Markdown 解析：清洗 + 块级结构 + 可朗读句子。

设计：
  - parse_to_blocks(text): 产出块级结构（标题/段落/列表/引用/代码/表格/分隔线）
  - parse_markdown(text): 从块 flatten 出可朗读句子列表，作为 TTS 的权威序列
  - 渲染器(markdown_render)也基于同一套块结构与句子切分，保证"句子索引"对齐
"""
import re

# 句末标点（中英文）
_SENT_SPLIT = re.compile(r'(?<=[。！？!?；;])')
# 行内 markdown 标记清理
_IMG = re.compile(r'!\[[^\]]*\]\([^)]*\)')
_LINK = re.compile(r'\[([^\]]*)\]\([^)]*\)')
_BOLD_ITALIC = re.compile(r'(\*{1,3}|_{1,3})(.+?)\1')
_INLINE_CODE = re.compile(r'`([^`]*)`')
_HTML_TAG = re.compile(r'<[^>]+>')
_TASK_BOX = re.compile(r'^\s*[-*+]\s+\[[ xX]\]\s*')
_LIST_MARK = re.compile(r'^\s*([-*+]|\d+[.)])\s+')
_HEADING = re.compile(r'^\s*(#{1,6})\s*')
_QUOTE = re.compile(r'^\s*>+\s?')
_HR = re.compile(r'^\s*([-*_])\s*(\1\s*){2,}$')
_TABLE_SEP = re.compile(r'^\s*\|?\s*:?-{2,}.*$')
_HAS_WORD = re.compile(r'[\w\u4e00-\u9fff]')


def _clean_inline(text: str) -> str:
    """清理一行里的行内 markdown 语法。"""
    text = _IMG.sub('', text)
    text = _LINK.sub(r'\1', text)
    for _ in range(3):
        new = _BOLD_ITALIC.sub(r'\2', text)
        if new == text:
            break
        text = new
    text = _INLINE_CODE.sub(r'\1', text)
    text = _HTML_TAG.sub('', text)
    text = text.replace('*', '').replace('`', '')
    return text.strip()


def _split_sentences(text: str, max_len: int = 80):
    """把较长的文本按句末标点切成短句，便于定位与朗读。"""
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_len:
        return [text]
    parts = [p.strip() for p in _SENT_SPLIT.split(text) if p.strip()]
    merged = []
    buf = ''
    for p in parts:
        if len(buf) + len(p) <= max_len:
            buf += p
        else:
            if buf:
                merged.append(buf)
            buf = p
    if buf:
        merged.append(buf)
    return merged or [text]


def parse_to_blocks(md_text: str):
    """把 markdown 解析为块级结构列表。"""
    lines = md_text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    blocks = []
    in_code = False
    code_buf = []
    para_buf = []
    quote_buf = []

    def flush_para():
        if para_buf:
            blocks.append({'type': 'paragraph', 'lines': list(para_buf)})
            para_buf.clear()

    def flush_quote():
        if quote_buf:
            blocks.append({'type': 'quote', 'lines': list(quote_buf)})
            quote_buf.clear()

    def flush_code():
        if code_buf:
            blocks.append({'type': 'code', 'lines': list(code_buf)})
            code_buf.clear()

    for raw in lines:
        line = raw.rstrip()

        # 代码块围栏
        if re.match(r'^\s*```', line):
            if in_code:
                flush_code()
                in_code = False
            else:
                flush_para()
                flush_quote()
                in_code = True
                code_buf = []
            continue
        if in_code:
            code_buf.append(line)
            continue

        stripped = line.strip()

        # 空行 -> 段落/引用分隔
        if not stripped:
            flush_para()
            flush_quote()
            continue

        # 水平线
        if _HR.match(stripped):
            flush_para()
            flush_quote()
            blocks.append({'type': 'hr'})
            continue

        # 表格分隔行（属于上一表格块）
        if _TABLE_SEP.match(stripped):
            continue

        # 标题
        m = _HEADING.match(stripped)
        if m:
            flush_para()
            flush_quote()
            level = len(m.group(1))
            title = _clean_inline(_HEADING.sub('', stripped))
            if title:
                blocks.append({'type': 'heading', 'level': level, 'text': title})
            continue

        # 引用（连续 > 行合并为一个引用块）
        if _QUOTE.match(stripped):
            flush_para()
            quote_buf.append(_QUOTE.sub('', stripped))
            continue

        # 表格数据行：| a | b |
        if stripped.startswith('|') and stripped.count('|') >= 2:
            flush_para()
            flush_quote()
            cells = [c.strip() for c in stripped.strip('|').split('|')]
            cells = [_clean_inline(c) for c in cells if c.strip()]
            if blocks and blocks[-1]['type'] == 'table':
                blocks[-1]['rows'].append(cells)
            else:
                blocks.append({'type': 'table', 'rows': [cells]})
            continue

        # 任务列表 / 普通列表
        if _TASK_BOX.match(stripped):
            flush_para()
            flush_quote()
            item = _clean_inline(_TASK_BOX.sub('', stripped))
            blocks.append({'type': 'list', 'ordered': False, 'text': item})
            continue
        if _LIST_MARK.match(stripped):
            flush_para()
            flush_quote()
            m2 = _LIST_MARK.match(stripped)
            ordered = bool(re.match(r'\d', m2.group(1)))
            item = _clean_inline(_LIST_MARK.sub('', stripped))
            blocks.append({'type': 'list', 'ordered': ordered, 'text': item})
            continue

        # 普通段落文字（累积，遇空行再切）
        para_buf.append(stripped)

    flush_para()
    flush_quote()
    flush_code()
    return blocks


def _block_sentences(block):
    """返回该块可朗读的句子列表（未过滤，顺序即朗读顺序）。"""
    t = block['type']
    if t == 'heading':
        return [block['text']] if block.get('text') else []
    if t == 'paragraph':
        joined = ' '.join(block['lines'])
        return _split_sentences(_clean_inline(joined))
    if t == 'quote':
        joined = ' '.join(block['lines'])
        return _split_sentences(_clean_inline(joined))
    if t == 'list':
        return _split_sentences(block['text'])
    if t == 'table':
        out = []
        for row in block['rows']:
            txt = '，'.join(row)
            if txt:
                out.append(txt)
        return out
    return []  # code / hr 不朗读


def parse_markdown(md_text: str):
    """把 markdown 解析为可朗读单元列表（TTS 权威序列）。

    返回: list[dict]，每项 {'text': 朗读文本, 'kind': 类型}
    """
    blocks = parse_to_blocks(md_text)
    units = []
    for b in blocks:
        for s in _block_sentences(b):
            if s and _HAS_WORD.search(s):
                units.append({'text': s, 'kind': b['type']})
    return units


if __name__ == '__main__':
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else None
    if path:
        with open(path, encoding='utf-8') as f:
            data = parse_markdown(f.read())
        for i, u in enumerate(data):
            print(f'[{i:03d}][{u["kind"]:>7}] {u["text"]}')
        print(f'\n共 {len(data)} 句')
