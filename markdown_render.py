# -*- coding: utf-8 -*-
"""标准 markdown 渲染（markdown 库 + pygments 高亮），供 QWebEngineView 展示。

设计要点：
  - 复用 md_parser 的块结构与句子切分 / 过滤，保证渲染的句子 idx 与
    parse_markdown 的朗读序列严格对齐（同一套切分 + 同一套过滤）。
  - 每句可朗读文本包成 <span class="s" data-idx="N">，点击时通过自定义
    mdtts:N 协议通知 Python（见 main_window 的 _Page），高亮通过前端 JS。
  - 图片使用 CSS max-width:100%; height:auto 自适应，无需按宽度重渲染，
    因此彻底消除 QTextBrowser 时代的「加载即重排 → 滚动条跳动」问题。
"""
import html
import os
import re
import sys
from pathlib import Path
import markdown as _md
from pygments import highlight
from pygments.lexers import get_lexer_by_name, TextLexer
from pygments.formatters import HtmlFormatter
from md_parser import (
    parse_to_blocks, _IMG, _resolve, _plain, _HAS_WORD,
    _split_sentences,
)

# pygments github 风格代码配色（一次生成）
_PYG_CSS = HtmlFormatter(style='xcode').get_style_defs('.highlight')

# GitHub 风格的明色主题 CSS
_GITHUB_CSS = """
:root{
  --fg:#24292f; --muted:#57606a; --border:#d0d7de; --code-bg:#f6f8fa;
  --link:#0969da; --hl:#fff8c5; --quote-bg:#f6f8fa;
}
*{box-sizing:border-box;}
body{font-family:-apple-system,'Segoe UI','Microsoft YaHei',Helvetica,Arial,sans-serif;
     line-height:1.7; color:var(--fg); max-width:980px; margin:0 auto;
     padding:20px 24px; font-size:15px;}
h1,h2{border-bottom:1px solid var(--border); padding-bottom:.3em; margin-top:24px;}
h1{font-size:1.9em;} h2{font-size:1.5em;} h3{font-size:1.25em;}
h4{font-size:1em;} h5,h6{font-size:.9em; color:var(--muted);}
p{margin:0 0 12px;}
a{color:var(--link); text-decoration:none;}
a:hover{text-decoration:underline;}
ul,ol{padding-left:2em; margin:0 0 12px;}
li{margin:4px 0;}
blockquote{margin:0 0 12px; padding:.5em 1em; border-left:4px solid var(--border);
          background:var(--quote-bg); color:var(--muted);}
code{background:rgba(175,184,193,.2); padding:.2em .4em; border-radius:6px;
     font-family:ui-monospace,SFMono-Regular,Consolas,'Courier New',monospace; font-size:85%;}
pre{margin:0 0 14px;}
pre code{background:transparent; padding:0; font-size:13px;}
.highlight{background:transparent!important; border:1px solid var(--border);
           border-radius:8px; margin:0 0 14px; overflow:auto;}
.highlight pre{margin:0; padding:12px 14px; background:var(--code-bg);
               font-family:ui-monospace,SFMono-Regular,Consolas,monospace; font-size:13px;}
.codeblock{border-radius:8px; overflow:hidden; margin:0 0 14px; background:var(--code-bg);}
.codelang{background:#eaeef2; color:var(--muted); font-size:11px;
          font-family:ui-monospace,monospace; padding:4px 12px;
          border-bottom:1px solid var(--border); letter-spacing:.3px;}
img{max-width:100%; height:auto; border:1px solid var(--border);
    border-radius:6px; display:block; margin:10px 0;}
.imgwrap{margin:10px 0;}
table{border-collapse:collapse; margin:0 0 14px; display:block;
      width:max-content; max-width:100%; overflow:auto;}
th,td{border:1px solid var(--border); padding:6px 13px;}
th{background:var(--code-bg); font-weight:600;}
tr:nth-child(2n) td{background:var(--quote-bg);}
hr{border:none; border-top:1px solid var(--border); margin:20px 0;}
.s{border-radius:3px; cursor:pointer; padding:0 1px; transition:background .12s;}
.s:hover{background:#ddf4ff;}
.s.cur{background:var(--hl);}
"""

_GITHUB_CSS += """
pre.mermaid{background:#fff; border:1px solid var(--border); border-radius:8px;
            margin:0 0 14px; padding:12px; overflow:auto;}
pre.mermaid svg{max-width:100%; height:auto;}
.mermaid-error{color:#cf222e; white-space:pre-wrap;}
"""


def _asset_url(*parts):
    """返回随程序发布的静态资源 file URL（兼容 PyInstaller）。"""
    root = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return Path(root, *parts).resolve().as_uri()


_MERMAID_URL = _asset_url('assets', 'mermaid', 'mermaid.min.js')
_CSS = '<style>' + _PYG_CSS + '\n' + _GITHUB_CSS + '</style>'
_MERMAID_SCRIPT = '<script src="%s"></script>' % html.escape(_MERMAID_URL, quote=True)

_JS = """
<script>
function highlightSentence(i){
  var old = document.querySelectorAll('.s.cur');
  for(var k=0;k<old.length;k++) old[k].classList.remove('cur');
  var el = document.querySelector('.s[data-idx="'+i+'"]');
  if(el){ el.classList.add('cur'); el.scrollIntoView({block:'center', behavior:'smooth'}); }
}
function clearHighlight(){
  var old = document.querySelectorAll('.s.cur');
  for(var k=0;k<old.length;k++) old[k].classList.remove('cur');
}
window.addEventListener('load', function(){
  if(!window.mermaid) return;
  mermaid.initialize({startOnLoad:false, securityLevel:'strict'});
  mermaid.run({nodes:document.querySelectorAll('pre.mermaid')}).catch(function(error){
    console.error('Mermaid rendering failed:', error);
    var nodes=document.querySelectorAll('pre.mermaid');
    for(var i=0;i<nodes.length;i++){
      if(nodes[i].querySelector('svg')) continue;
      nodes[i].classList.add('mermaid-error');
      nodes[i].title='Mermaid 图表语法错误；请查看开发者控制台获取详情。';
    }
  });
});
</script>
"""

# 句子 span 的 class / data-idx / onclick，保证 HTML 属性双引号、JS 用单引号不冲突
def _s_attr(idx):
    return ('class="s" data-idx="%d" '
            "onclick=\"if(event.target.tagName==='A')return;location.href='mdtts:%d'\"" % (idx, idx))


def _inline_md(text, base_dir=''):
    """用标准 markdown 库渲染行内格式（加粗/斜体/链接/行内代码）。"""
    # 先把相对图片路径换成 file:/// 绝对路径，让 markdown 库原样保留
    def fix_img(m):
        alt, src = m.group(1), m.group(2)
        if re.match(r'^(https?:|data:|//)', src):
            return m.group(0)
        return '![%s](%s)' % (alt, _resolve(src, base_dir))
    text = _IMG.sub(fix_img, text)
    h = _md.markdown(text, extensions=[])
    # 去掉 markdown 库自动包裹的 <p>...</p>
    h = re.sub(r'^<p>(.*)</p>\s*$', r'\1', h, flags=re.S)
    return h


def _render_code(code, lang):
    try:
        lexer = get_lexer_by_name(lang, stripall=False)
    except Exception:
        lexer = TextLexer()
    fmt = HtmlFormatter(style='xcode', nowrap=False)
    return highlight(code, lexer, fmt)


def render_markdown(blocks, base_dir=''):
    """返回渲染好的完整 HTML 字符串（供 QWebEngineView 加载）。

    base_dir 用于解析相对图片路径，并作为 setHtml 的 baseUrl。
    每句可朗读文本包 <span class="s" data-idx="N">，N 与 parse_markdown 对齐。
    """
    out = ['<html><head><meta charset="utf-8">', _CSS, _MERMAID_SCRIPT, _JS, '</head><body>']
    idx = 0
    i = 0
    n = len(blocks)
    while i < n:
        b = blocks[i]
        t = b['type']

        if t == 'heading':
            lvl = max(1, min(6, b.get('level', 1)))
            raw = b.get('text', '')
            if raw.strip() and _HAS_WORD.search(_plain(raw)):
                out.append('<h%d><span %s>%s</span></h%d>'
                           % (lvl, _s_attr(idx), _inline_md(raw, base_dir), lvl))
                idx += 1
            i += 1

        elif t == 'paragraph':
            for line in b['lines']:
                for m in _IMG.finditer(line):
                    src = _resolve(m.group(2), base_dir)
                    alt = html.escape(m.group(1))
                    out.append('<div class="imgwrap"><img src="%s" alt="%s"/></div>'
                               % (src, alt))
            joined = ' '.join(b['lines'])
            for s in _split_sentences(_IMG.sub('', joined)):
                p = _plain(s)
                if p and _HAS_WORD.search(p):
                    out.append('<p><span %s>%s</span></p>'
                               % (_s_attr(idx), _inline_md(s, base_dir)))
                    idx += 1
            i += 1

        elif t == 'quote':
            joined = ' '.join(b['lines'])
            for s in _split_sentences(_IMG.sub('', joined)):
                p = _plain(s)
                if p and _HAS_WORD.search(p):
                    out.append('<blockquote><span %s>%s</span></blockquote>'
                               % (_s_attr(idx), _inline_md(s, base_dir)))
                    idx += 1
            i += 1

        elif t == 'list':
            ordered = b.get('ordered', False)
            tag = 'ol' if ordered else 'ul'
            out.append('<%s>' % tag)
            while i < n and blocks[i]['type'] == 'list' and \
                    blocks[i].get('ordered', False) == ordered:
                for s in _split_sentences(blocks[i]['text']):
                    p = _plain(s)
                    if p and _HAS_WORD.search(p):
                        out.append('<li><span %s>%s</span></li>'
                                   % (_s_attr(idx), _inline_md(s, base_dir)))
                        idx += 1
                i += 1
            out.append('</%s>' % tag)

        elif t == 'table':
            out.append('<table><tbody>')
            for row in b['rows']:
                tds = ''
                for ci, c in enumerate(row):
                    cell = _inline_md(c, base_dir)
                    if ci == 0:
                        tds += '<td><span %s>%s</span></td>' % (_s_attr(idx), cell)
                    else:
                        tds += '<td>%s</td>' % cell
                out.append('<tr>%s</tr>' % tds)
                txt = '，'.join(_plain(c) for c in row)
                if txt and _HAS_WORD.search(txt):
                    idx += 1
            out.append('</tbody></table>')
            i += 1

        elif t == 'code':
            code = '\n'.join(b['lines'])
            lang = b.get('lang', '').strip()
            if lang.lower() == 'mermaid':
                # Mermaid 接收未被 Markdown/Pygments 改写的源码；HTML 转义避免
                # 用户文档中的标签被当作页面脚本执行。
                out.append('<pre class="mermaid">%s</pre>' % html.escape(code))
            else:
                lang_html = '<div class="codelang">%s</div>' % html.escape(lang) if lang else ''
                out.append('<div class="codeblock">%s%s</div>'
                           % (lang_html, _render_code(code, lang)))
            i += 1

        elif t == 'hr':
            out.append('<hr>')
            i += 1

        else:
            i += 1

    out.append('</body></html>')
    return ''.join(out)
