# -*- coding: utf-8 -*-
"""把 blocks 渲染成规范、美观的 HTML（浅色主题）。

关键：每句可朗读文本包成 <a name="u{idx}">，idx 与 md_parser.parse_markdown
的句子序列严格对齐（相同切分 + 相同过滤），这样点击渲染视图就能精确定位到
对应的朗读句子。
"""
import html
from md_parser import _clean_inline, _split_sentences, _HAS_WORD

esc = html.escape

_CSS = """
<style>
  body{font-family:'Microsoft YaHei','PingFang SC',sans-serif;line-height:1.75;
       color:#222;max-width:860px;margin:14px 18px;font-size:15px;}
  h1{font-size:25px;border-bottom:2px solid #4a90d9;padding-bottom:6px;
     margin:20px 0 12px;color:#15466e;}
  h2{font-size:20px;margin:18px 0 9px;color:#1a5d8a;
     border-left:4px solid #4a90d9;padding-left:9px;}
  h3{font-size:17px;margin:15px 0 7px;color:#20617f;}
  h4,h5,h6{font-size:15px;margin:13px 0 6px;color:#333;}
  p{margin:9px 0;}
  ul,ol{margin:7px 0;padding-left:26px;}
  li{margin:4px 0;}
  blockquote{margin:9px 0;padding:7px 13px;border-left:4px solid #b9c7d3;
       background:#f4f7fa;color:#54616b;border-radius:0 4px 4px 0;}
  pre{background:#2d2d2d;color:#f8f8f2;padding:11px 13px;border-radius:6px;
      overflow:auto;font-family:Consolas,'Courier New',monospace;font-size:13px;}
  code{background:#eef0f2;padding:1px 5px;border-radius:3px;
       font-family:Consolas,'Courier New',monospace;font-size:13px;color:#c0392b;}
  pre code{background:none;padding:0;color:inherit;}
  table{border-collapse:collapse;margin:10px 0;font-size:13.5px;}
  th,td{border:1px solid #cfd8e0;padding:6px 10px;}
  th{background:#eaf2fb;}
  tr:nth-child(even) td{background:#f7fafc;}
  hr{border:none;border-top:1px solid #e0e0e0;margin:16px 0;}
  a.sentence{color:inherit;text-decoration:none;cursor:text;border-radius:3px;}
</style>
"""


def render_markdown(blocks):
    """返回渲染好的完整 HTML 字符串。"""
    out = ['<html><head>', _CSS, '</head><body>']
    idx = 0
    i = 0
    n = len(blocks)
    while i < n:
        b = blocks[i]
        t = b['type']

        if t == 'heading':
            lvl = max(1, min(6, b.get('level', 1)))
            txt = b.get('text', '')
            if txt and _HAS_WORD.search(txt):
                out.append(f'<h{lvl}><a name="u{idx}" class="sentence">{esc(txt)}</a></h{lvl}>')
                idx += 1
            i += 1

        elif t == 'paragraph':
            joined = ' '.join(b['lines'])
            for s in _split_sentences(_clean_inline(joined)):
                if s and _HAS_WORD.search(s):
                    out.append(f'<p><a name="u{idx}" class="sentence">{esc(s)}</a></p>')
                    idx += 1
            i += 1

        elif t == 'quote':
            joined = ' '.join(b['lines'])
            for s in _split_sentences(_clean_inline(joined)):
                if s and _HAS_WORD.search(s):
                    out.append(f'<blockquote><a name="u{idx}" class="sentence">{esc(s)}</a></blockquote>')
                    idx += 1
            i += 1

        elif t == 'list':
            ordered = b.get('ordered', False)
            tag = 'ol' if ordered else 'ul'
            out.append(f'<{tag}>')
            while i < n and blocks[i]['type'] == 'list' and blocks[i].get('ordered', False) == ordered:
                for s in _split_sentences(blocks[i]['text']):
                    if s and _HAS_WORD.search(s):
                        out.append(f'<li><a name="u{idx}" class="sentence">{esc(s)}</a></li>')
                        idx += 1
                i += 1
            out.append(f'</{tag}>')

        elif t == 'table':
            out.append('<table><tbody>')
            for row in b['rows']:
                tds = ''
                for ci, c in enumerate(row):
                    if ci == 0:
                        tds += f'<td><a name="u{idx}" class="sentence">{esc(c)}</a></td>'
                    else:
                        tds += f'<td>{esc(c)}</td>'
                out.append(f'<tr>{tds}</tr>')
                txt = '，'.join(row)
                if txt and _HAS_WORD.search(txt):
                    idx += 1
            out.append('</tbody></table>')
            i += 1

        elif t == 'code':
            code = '\n'.join(b['lines'])
            out.append(f'<pre><code>{esc(code)}</code></pre>')
            i += 1

        elif t == 'hr':
            out.append('<hr>')
            i += 1

        else:
            i += 1

    out.append('</body></html>')
    return ''.join(out)
