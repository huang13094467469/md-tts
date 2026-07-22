# -*- coding: utf-8 -*-
"""Markdown 解析：清洗 + 块级结构 + 可朗读句子。

设计：
  - parse_to_blocks(text): 产出块级结构（标题/段落/列表/引用/代码/表格/分隔线）
  - parse_markdown(text): 从块 flatten 出可朗读句子列表，作为 TTS 的权威序列
  - markdown_render 也基于同一套块结构与句子切分，保证"句子索引"对齐

句子切分统一在「原始带标记文本」上做，TTS 用 _plain() 去标记得到朗读文本，
渲染用 _inline_html() 把标记转成样式标签，两者索引严格一致。
"""
import re
import os
import html

# 句末标点（中英文）
_SENT_SPLIT = re.compile(r'(?<=[。！？!?；;])')
# 行内 markdown 标记
_IMG = re.compile(r'!\[([^\]]*)\]\(([^)]*)\)')
_LINK = re.compile(r'\[([^\]]*)\]\(([^)]*)\)')
_BOLD = re.compile(r'\*\*(.+?)\*\*')
_BOLD2 = re.compile(r'__(.+?)__')
_ITAL_S = re.compile(r'(?<!\*)\*(?!\*)(.+?)\*(?!\*)')
_ITAL_U = re.compile(r'(?<!_)_(?!_)(.+?)_(?!_)')
_INLINE_CODE = re.compile(r'`([^`]+)`')
_HTML_TAG = re.compile(r'<[^>]+>')
_TASK_BOX = re.compile(r'^\s*[-*+]\s+\[[ xX]\]\s*')
_LIST_MARK = re.compile(r'^\s*([-*+]|\d+[.)])\s+')
_HEADING = re.compile(r'^\s*(#{1,6})\s*')
_QUOTE = re.compile(r'^\s*>+\s?')
_HR = re.compile(r'^\s*([-*_])\s*(\1\s*){2,}$')
_TABLE_SEP = re.compile(r'^\s*\|?\s*:?-{2,}.*$')
# 围栏开栏：3 个及以上反引号，捕获反引号串与语言标注（支持 CommonMark 嵌套围栏）
_FENCE_OPEN = re.compile(r'^\s*(`{3,})\s*([\w+-]*)')
_HAS_WORD = re.compile(r'[\w\u4e00-\u9fff]')


def _is_fence_close(line: str, fence: str) -> bool:
    """判断该行是否为匹配的闭栏：整行去除空白后全由反引号组成，且数量 >= 开栏。

    CommonMark 规定闭栏反引号数量需 >= 开栏，此处据此实现，使得
    ````md ... ``` ... ```` 中内层的 3 反引号不会被误判为外层 4 反引号块的结束。
    """
    s = line.strip()
    return bool(s) and set(s) == {'`'} and len(s) >= len(fence)


def _resolve(src: str, base_dir: str = '') -> str:
    """把图片/链接地址解析成可用于 <img src> 的 URL。

    - 远程(http/https/data)、协议相对(//) 原样返回
    - 绝对路径直接用
    - 相对路径基于 markdown 文件所在目录解析成 file:/// 绝对路径
    """
    if re.match(r'^(https?:|data:|//)', src):
        return src
    if os.path.isabs(src):
        path = src
    else:
        path = os.path.join(base_dir, src) if base_dir else src
    path = os.path.abspath(path)
    return 'file:///' + path.replace('\\', '/')


def _plain(text: str) -> str:
    """清理一行里的行内 markdown 语法，得到「纯文本」（供朗读）。"""
    text = _IMG.sub('', text)
    text = _LINK.sub(r'\1', text)
    text = _BOLD.sub(r'\1', text)
    text = _BOLD2.sub(r'\1', text)
    text = _ITAL_S.sub(r'\1', text)
    text = _ITAL_U.sub(r'\1', text)
    text = _INLINE_CODE.sub(r'\1', text)
    text = _HTML_TAG.sub('', text)
    text = text.replace('*', '').replace('`', '')
    return text.strip()


def _inline_html(text: str, base_dir: str = '') -> str:
    """把行内 markdown 转成带样式的 HTML（供显示）。

    先整体 HTML 转义，再套用标记 -> 标签，保证不会注入原始标签。
    """
    text = html.escape(text)
    # 图片 -> <img>（显示用，朗读时由 _plain 去除）
    text = _IMG.sub(lambda m: (
        f'<img class="md-img" src="{_resolve(m.group(2), base_dir)}" alt="{m.group(1)}"/>'
    ), text)
    text = _INLINE_CODE.sub(r'<code>\1</code>', text)
    text = _LINK.sub(r'<a href="\2">\1</a>', text)
    text = _BOLD.sub(r'<b>\1</b>', text)
    text = _BOLD2.sub(r'<b>\1</b>', text)
    text = _ITAL_S.sub(r'<i>\1</i>', text)
    text = _ITAL_U.sub(r'<i>\1</i>', text)
    return text


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
    code_lang = ''
    code_fence = ''
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
            blocks.append({'type': 'code', 'lang': code_lang, 'lines': list(code_buf)})
            code_buf.clear()

    for raw in lines:
        line = raw.rstrip()

        if in_code:
            # 代码块内：仅匹配的闭栏（同数量或更多反引号）才结束本块；
            # 内层更少数量的反引号行（如 ```` 块里的 ```）视作代码内容保留
            if _is_fence_close(line, code_fence):
                flush_code()
                in_code = False
                code_fence = ''
                continue
            code_buf.append(line)
            continue

        # 代码块开栏（3 个及以上反引号，记录语言与开栏反引号串）
        fm = _FENCE_OPEN.match(line)
        if fm:
            flush_para()
            flush_quote()
            in_code = True
            code_buf = []
            code_fence = fm.group(1)
            code_lang = fm.group(2) or ''
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

        # 标题（保留行内标记，供渲染/朗读时各自处理）
        m = _HEADING.match(stripped)
        if m:
            flush_para()
            flush_quote()
            level = len(m.group(1))
            title = _HEADING.sub('', stripped)
            if title.strip():
                blocks.append({'type': 'heading', 'level': level, 'text': title})
            continue

        # 引用
        if _QUOTE.match(stripped):
            flush_para()
            quote_buf.append(_QUOTE.sub('', stripped))
            continue

        # 表格数据行：| a | b |
        if stripped.startswith('|') and stripped.count('|') >= 2:
            flush_para()
            flush_quote()
            cells = [c.strip() for c in stripped.strip('|').split('|')]
            cells = [c for c in cells if c.strip()]
            if blocks and blocks[-1]['type'] == 'table':
                blocks[-1]['rows'].append(cells)
            else:
                blocks.append({'type': 'table', 'rows': [cells]})
            continue

        # 任务列表 / 普通列表
        if _TASK_BOX.match(stripped):
            flush_para()
            flush_quote()
            item = _TASK_BOX.sub('', stripped)
            blocks.append({'type': 'list', 'ordered': False, 'text': item})
            continue
        if _LIST_MARK.match(stripped):
            flush_para()
            flush_quote()
            m2 = _LIST_MARK.match(stripped)
            ordered = bool(re.match(r'\d', m2.group(1)))
            item = _LIST_MARK.sub('', stripped)
            blocks.append({'type': 'list', 'ordered': ordered, 'text': item})
            continue

        # 普通段落文字
        para_buf.append(stripped)

    flush_para()
    flush_quote()
    flush_code()
    return blocks


def _block_sentences(block):
    """返回该块「原始带标记」的可朗读句子列表（顺序即朗读顺序）。"""
    t = block['type']
    if t == 'heading':
        return [block['text']] if block.get('text', '').strip() else []
    if t == 'paragraph':
        joined = ' '.join(block['lines'])
        return _split_sentences(_IMG.sub('', joined))
    if t == 'quote':
        joined = ' '.join(block['lines'])
        return _split_sentences(_IMG.sub('', joined))
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

    返回: list[dict]，每项 {'text': 朗读纯文本, 'kind': 类型}
    """
    blocks = parse_to_blocks(md_text)
    units = []
    for b in blocks:
        for s in _block_sentences(b):
            p = _plain(s)
            if p and _HAS_WORD.search(p):
                units.append({'text': p, 'kind': b['type']})
    return units


# ----------------- MDX 预处理 -----------------
_MDX_FRONTMATTER = re.compile(r'^\ufeff?---\n.*?\n---\s*\n', re.DOTALL)
_MDX_IMPORT_EXPORT = re.compile(r'^\s*(import|export)\b[^\n]*$', re.MULTILINE)
_MDX_PAIR_TAG = re.compile(
    r'<[A-Z][A-Za-z0-9.]*\b[^>]*>(.*?)</[A-Z][A-Za-z0-9.]*>', re.DOTALL)
_MDX_SELF_TAG = re.compile(r'<[A-Z][A-Za-z0-9.]*\b[^>]*/>')
_MDX_CURLY = re.compile(r'\{.*?\}', re.DOTALL)

# 围栏代码块 / 行内代码：在 MDX 清洗前抽离、清洗后还原，避免代码内容被误删
# 围栏块用行扫描抽离（见 _mdx_stash_fences），以正确处理 3+ 反引号嵌套；
# 行内代码用正则即可
_MDX_INLINE_CODE = re.compile(r'`[^`\n]+`')
_MDX_SLOT = re.compile(r'\x00(\d+)\x00')


def _mdx_stash_fences(text: str, slots: list) -> str:
    """行扫描抽离所有围栏代码块（支持 3+ 反引号嵌套），替换为占位符。

    与 _FENCE_OPEN / _is_fence_close 配对，确保 ````md ... ``` ... ````
    中内层 3 反引号不会被误当作外层 4 反引号块的结束，从而完整保护代码内容。
    抽离内容含开/闭栏行，还原后 parse_to_blocks 可正常识别。
    """
    lines = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    out = []
    i = 0
    n = len(lines)
    while i < n:
        m = _FENCE_OPEN.match(lines[i])
        if m:
            fence = m.group(1)
            buf = [lines[i]]
            i += 1
            while i < n:
                buf.append(lines[i])
                if _is_fence_close(lines[i], fence):
                    i += 1
                    break
                i += 1
            slots.append('\n'.join(buf))
            out.append('\x00%d\x00' % (len(slots) - 1))
        else:
            out.append(lines[i])
            i += 1
    return '\n'.join(out)


def preprocess_mdx(text: str) -> str:
    """把 MDX 当作可朗读的 Markdown 处理。

    MDX = Markdown + JSX。朗读/预览场景下需剥离「非朗读内容」：
      - 顶部 frontmatter（--- ... --- 元信息）
      - 顶层 import/export 语句
      - JSX 组件标签（成对 -> 保留内部文字，自闭合 -> 删除）
      - JSX 表达式 {...}
    保留纯文本，使现有 markdown 流水线（分句/渲染）可直接复用。

    注意：上述清洗只应作用于正文中的 JSX 语法，围栏代码块与行内代码
    的内容必须原样保留（代码里出现 {}、import、<Tag> 等都是合法的），
    因此先做占位抽离，清洗后再还原。
    """
    slots = []

    def _stash(m):
        slots.append(m.group(0))
        return '\x00%d\x00' % (len(slots) - 1)

    text = _mdx_stash_fences(text, slots)
    text = _MDX_INLINE_CODE.sub(_stash, text)

    text = _MDX_FRONTMATTER.sub('', text)
    text = _MDX_IMPORT_EXPORT.sub('', text)
    # 成对组件标签 -> 保留内部，循环处理嵌套
    prev = None
    while prev != text:
        prev = text
        text = _MDX_PAIR_TAG.sub(r'\1', text)
    # 自闭合组件标签 -> 删除
    text = _MDX_SELF_TAG.sub('', text)
    # 剩余 JSX 表达式 -> 删除
    text = _MDX_CURLY.sub('', text)
    # 压缩多余空行
    text = re.sub(r'\n{3,}', '\n\n', text)

    text = _MDX_SLOT.sub(lambda m: slots[int(m.group(1))], text)
    return text.strip() + '\n'


if __name__ == '__main__':
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else None
    if path:
        with open(path, encoding='utf-8') as f:
            data = parse_markdown(f.read())
        for i, u in enumerate(data):
            print(f'[{i:03d}][{u["kind"]:>7}] {u["text"]}')
        print(f'\n共 {len(data)} 句')
