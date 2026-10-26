"""Offline user manuals, native help viewer, and print-ready HTML editions."""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
import tkinter as tk
from tkinter import font as tkfont, ttk
import webbrowser

BASE_DIR = Path(__file__).resolve().parent
MANUAL_DIR = BASE_DIR / 'manuals'
TOOL_IDS = ('bjt', 'arrange', 'overlay', 'exchanger', 'alignment', 'thermal', 'compare', 'number_tools', 'sequence_z', 'm2_converter', 'chitu_checker')
BLOCK_TYPES = {'paragraph', 'heading', 'note', 'steps', 'bullets', 'table', 'code'}


def load_manual(key):
    if key not in TOOL_IDS:
        raise ValueError('알 수 없는 도구: ' + str(key))
    manual = json.loads((MANUAL_DIR / (key + '.json')).read_text(encoding='utf-8'))
    if manual.get('id') != key or not manual.get('title') or not manual.get('sections'):
        raise ValueError('사용설명서 구성이 올바르지 않습니다: ' + key)
    section_ids = set()
    for section in manual['sections']:
        identity = section['id']
        if identity in section_ids or not identity.replace('-', '').replace('_', '').isascii() or not identity:
            raise ValueError('목차 식별자가 올바르지 않습니다: ' + key)
        section_ids.add(identity)
        if not section.get('title') or not section.get('blocks'):
            raise ValueError('빈 설명서 장: ' + key)
        for block in section['blocks']:
            if block.get('type') not in BLOCK_TYPES:
                raise ValueError('지원하지 않는 설명서 구성: ' + key)
            if block['type'] == 'table':
                if not block.get('headers') or any(len(row) != len(block['headers']) for row in block['rows']):
                    raise ValueError('표 열 수가 일치하지 않습니다: ' + key)
    return manual


def manual_plain_text(manual):
    lines = [manual['title'], manual.get('subtitle', ''), '목차']
    lines.extend(section['title'] for section in manual['sections'])
    for section in manual['sections']:
        lines.extend(('', section['title']))
        for block in section['blocks']:
            kind = block['type']
            if kind in {'paragraph', 'heading', 'note', 'code'}:
                lines.append(block['text'])
            elif kind in {'steps', 'bullets'}:
                lines.extend(f'{index}. {item}' if kind == 'steps' else '• ' + item
                             for index, item in enumerate(block['items'], 1))
            else:
                lines.append(' | '.join(block['headers']))
                lines.extend(' | '.join(str(cell) for cell in row) for row in block['rows'])
    return '\n'.join(lines)


class ManualWindow(tk.Toplevel):
    def __init__(self, master, key):
        self.manual = load_manual(key)
        self.tool_key = key
        super().__init__(master)
        self.title(self.manual['title'] + ' · BJT 통합 작업 도구')
        self.geometry(f'{min(1280, self.winfo_screenwidth() - 80)}x{min(900, self.winfo_screenheight() - 100)}')
        self.minsize(920, 580)
        self.body_font = tkfont.Font(root=self, family='맑은 고딕', size=11)
        self.size = 11
        self.query = tk.StringVar(master=self)
        self.result_text = tk.StringVar(master=self, value='목차에서 장을 선택하거나 본문을 검색하세요.')
        self.matches = []
        self.match_index = -1
        self.last_query = ''
        self.current_section = 0
        self._build()
        self._render()
        self.bind('<Control-f>', self.focus_search)
        self.bind('<F3>', lambda event: self.next_match(1))
        self.bind('<Shift-F3>', lambda event: self.next_match(-1))
        self.bind('<Escape>', lambda event: self.destroy())
        self.bind('<Control-plus>', lambda event: self.change_size(1))
        self.bind('<Control-minus>', lambda event: self.change_size(-1))

    def _build(self):
        header = ttk.Frame(self, padding=(20, 15, 20, 12))
        header.pack(fill='x')
        ttk.Label(header, text=self.manual['title'], font=('맑은 고딕', 18, 'bold')).pack(anchor='w')
        ttk.Label(header, text=self.manual.get('subtitle', ''), wraplength=1050).pack(anchor='w', pady=(5, 0))
        date = self.manual.get('document_date', '2026.10.21').replace('-', '.')
        ttk.Label(header, text=f"사용설명서 · 문서 {self.manual.get('document_version', '1.1')} · {date}",
                  foreground='#5b6779').pack(anchor='w', pady=(6, 0))
        toolbar = ttk.Frame(self, padding=(16, 0, 16, 10))
        toolbar.pack(fill='x')
        ttk.Label(toolbar, text='본문 검색').pack(side='left', padx=(0, 6))
        self.search_entry = ttk.Entry(toolbar, textvariable=self.query, width=24)
        self.search_entry.pack(side='left')
        self.search_entry.bind('<Return>', lambda event: self.find_text())
        ttk.Button(toolbar, text='검색', command=self.find_text).pack(side='left', padx=(5, 10))
        self.previous_result = ttk.Button(toolbar, text='이전 결과', command=lambda: self.next_match(-1), state='disabled')
        self.previous_result.pack(side='left')
        self.next_result = ttk.Button(toolbar, text='다음 결과', command=lambda: self.next_match(1), state='disabled')
        self.next_result.pack(side='left', padx=(5, 10))
        ttk.Button(toolbar, text='A−', width=4, command=lambda: self.change_size(-1)).pack(side='left')
        ttk.Button(toolbar, text='A+', width=4, command=lambda: self.change_size(1)).pack(side='left', padx=(3, 0))
        ttk.Button(toolbar, text='닫기', command=self.destroy).pack(side='right')
        ttk.Button(toolbar, text='브라우저·인쇄', command=self.open_browser).pack(side='right', padx=6)
        panes = ttk.Panedwindow(self, orient='horizontal')
        panes.pack(fill='both', expand=True, padx=16)
        sidebar = ttk.Frame(panes, width=265)
        panes.add(sidebar, weight=0)
        ttk.Label(sidebar, text='목차', font=('맑은 고딕', 11, 'bold'), padding=(7, 5)).pack(anchor='w')
        self.toc = ttk.Treeview(sidebar, show='tree', selectmode='browse')
        self.toc.column('#0', width=340, minwidth=220)
        scroll = ttk.Scrollbar(sidebar, orient='vertical', command=self.toc.yview)
        xscroll = ttk.Scrollbar(sidebar, orient='horizontal', command=self.toc.xview)
        self.toc.configure(yscrollcommand=scroll.set, xscrollcommand=xscroll.set)
        xscroll.pack(side='bottom', fill='x')
        scroll.pack(side='right', fill='y')
        self.toc.pack(fill='both', expand=True)
        for section in self.manual['sections']:
            self.toc.insert('', 'end', iid=section['id'], text=section['title'])
        self.toc.bind('<<TreeviewSelect>>', self._toc_selected)
        self.toc.bind('<ButtonRelease-1>', lambda event: self._toc_selected(force=True))
        content = ttk.Frame(panes)
        panes.add(content, weight=1)
        self.text = tk.Text(content, wrap='word', font=self.body_font, background='#ffffff',
                            foreground='#1d2a3b', borderwidth=1, relief='solid', padx=22, pady=20,
                            spacing1=2, spacing3=5, selectbackground='#c8dffc', undo=False)
        yscroll = ttk.Scrollbar(content, orient='vertical', command=self.text.yview)
        self.text.configure(yscrollcommand=yscroll.set)
        yscroll.pack(side='right', fill='y')
        self.text.pack(fill='both', expand=True)
        self.text.tag_configure('chapter', font=('맑은 고딕', 16, 'bold'), foreground='#123e68', spacing1=22, spacing3=12)
        self.text.tag_configure('subheading', font=('맑은 고딕', 12, 'bold'), foreground='#164e78', spacing1=12, spacing3=6)
        self.text.tag_configure('step', lmargin1=7, lmargin2=27, spacing3=9)
        self.text.tag_configure('bullet', lmargin1=7, lmargin2=25, spacing3=5)
        self.text.tag_configure('note', background='#eaf4fb', lmargin1=13, lmargin2=13, rmargin=10, spacing1=8, spacing3=12)
        self.text.tag_configure('warning', background='#fff2dc', lmargin1=13, lmargin2=13, rmargin=10, spacing1=8, spacing3=12)
        self.text.tag_configure('table_head', font=('맑은 고딕', 10, 'bold'), background='#edf2f7', spacing1=8, spacing3=8)
        self.text.tag_configure('table_key', font=('맑은 고딕', 11, 'bold'), lmargin1=8, lmargin2=8, spacing1=5)
        self.text.tag_configure('table_value', lmargin1=18, lmargin2=18, spacing3=3)
        self.text.tag_configure('code', font=('Consolas', 10), background='#f0f3f6', lmargin1=12, lmargin2=12, spacing1=8, spacing3=12)
        self.text.tag_configure('search_hit', background='#fff09b')
        self.text.tag_configure('search_current', background='#ffc663', foreground='#172232')
        self.text.bind('<Control-a>', self._select_all)
        bottom = ttk.Frame(self, padding=(16, 9, 16, 11))
        bottom.pack(fill='x')
        ttk.Button(bottom, text='이전 장', command=lambda: self.move_section(-1)).pack(side='left')
        ttk.Button(bottom, text='다음 장', command=lambda: self.move_section(1)).pack(side='left', padx=5)
        ttk.Label(bottom, textvariable=self.result_text).pack(side='left', padx=12)

    def _render(self):
        for section in self.manual['sections']:
            self.text.mark_set('chapter_' + section['id'], self.text.index('end-1c'))
            self.text.mark_gravity('chapter_' + section['id'], 'left')
            self.text.insert('end', section['title'] + '\n', 'chapter')
            for block in section['blocks']:
                kind = block['type']
                if kind == 'paragraph':
                    self.text.insert('end', block['text'] + '\n\n')
                elif kind == 'heading':
                    self.text.insert('end', block['text'] + '\n', 'subheading')
                elif kind == 'note':
                    label = '확인 사항' if block.get('level') == 'warning' else '참고'
                    self.text.insert('end', label + '\n' + block['text'] + '\n\n',
                                     'warning' if block.get('level') == 'warning' else 'note')
                elif kind in {'steps', 'bullets'}:
                    for index, item in enumerate(block['items'], 1):
                        prefix = f'{index}. ' if kind == 'steps' else '• '
                        self.text.insert('end', prefix + item + '\n', 'step' if kind == 'steps' else 'bullet')
                    self.text.insert('end', '\n')
                elif kind == 'code':
                    self.text.insert('end', block['text'] + '\n\n', 'code')
                else:
                    self.text.insert('end', '  ' + ' / '.join(block['headers']) + '\n', 'table_head')
                    for row in block['rows']:
                        self.text.insert('end', str(row[0]) + '\n', 'table_key')
                        for heading, value in zip(block['headers'][1:], row[1:]):
                            self.text.insert('end', heading + ': ' + str(value) + '\n', 'table_value')
                    self.text.insert('end', '\n')
        self.text.configure(state='disabled')
        first = self.manual['sections'][0]['id']
        self.toc.selection_set(first)
        self.toc.focus(first)
        self.text.yview_moveto(0)

    def _toc_selected(self, _event=None, *, force=False):
        selection = self.toc.selection()
        if selection:
            key = selection[0]
            index = next(i for i, item in enumerate(self.manual['sections']) if item['id'] == key)
            # Search updates the selected TOC row too. Its queued virtual event
            # must not scroll away from the highlighted search result.
            if index == self.current_section and not force:
                return
            self.current_section = index
            self.text.see('chapter_' + key)
            self.result_text.set(self.manual['sections'][self.current_section]['title'])

    def move_section(self, direction):
        index = max(0, min(len(self.manual['sections']) - 1, self.current_section + direction))
        key = self.manual['sections'][index]['id']
        self.toc.selection_set(key)
        self.toc.focus(key)
        self.toc.see(key)
        self._toc_selected(force=True)

    def focus_search(self, _event=None):
        self.search_entry.focus_set()
        self.search_entry.selection_range(0, 'end')
        return 'break'

    def find_text(self):
        needle = self.query.get().strip()
        if needle and needle == self.last_query and self.matches:
            return self.next_match(1)
        self.text.tag_remove('search_hit', '1.0', 'end')
        self.text.tag_remove('search_current', '1.0', 'end')
        self.matches = []
        self.match_index = -1
        self.last_query = needle
        if needle:
            position = '1.0'
            while True:
                found = self.text.search(needle, position, stopindex='end', nocase=True, regexp=False)
                if not found:
                    break
                end = self.text.index(f'{found}+{len(needle)}c')
                self.matches.append((found, end))
                self.text.tag_add('search_hit', found, end)
                position = end
        state = 'normal' if self.matches else 'disabled'
        self.previous_result.configure(state=state)
        self.next_result.configure(state=state)
        if self.matches:
            self.next_match(1)
        else:
            self.result_text.set('검색 결과가 없습니다.' if needle else '검색어를 입력하세요.')
        return 'break'

    def next_match(self, direction):
        if self.matches:
            self.text.tag_remove('search_current', '1.0', 'end')
            self.match_index = (self.match_index + direction) % len(self.matches)
            start, end = self.matches[self.match_index]
            self.text.tag_add('search_current', start, end)
            for index in range(len(self.manual['sections']) - 1, -1, -1):
                section = self.manual['sections'][index]
                if self.text.compare(start, '>=', 'chapter_' + section['id']):
                    self.current_section = index
                    self.toc.selection_set(section['id'])
                    self.toc.focus(section['id'])
                    self.toc.see(section['id'])
                    break
            self.text.see(start)
            self.result_text.set(f'검색 {self.match_index + 1} / {len(self.matches)}')
        return 'break'

    def change_size(self, amount):
        self.size = max(9, min(18, self.size + amount))
        self.body_font.configure(size=self.size)
        return 'break'

    def _select_all(self, _event=None):
        self.text.tag_add('sel', '1.0', 'end-1c')
        return 'break'

    def open_browser(self):
        return webbrowser.open((MANUAL_DIR / (self.tool_key + '.html')).resolve().as_uri())


STYLE = '''
:root{--ink:#1e2b3d;--muted:#617083;--blue:#164f7d;--line:#dce4ec;--paper:#fff;--background:#f3f6fa}
*{box-sizing:border-box}html{scroll-behavior:smooth;scroll-padding-top:92px}body{margin:0;color:var(--ink);background:var(--background);font:16px/1.8 "Malgun Gothic","Apple SD Gothic Neo",sans-serif}
a{color:var(--blue);text-underline-offset:3px}.topbar{position:sticky;top:0;z-index:10;background:#102b47;color:white;display:flex;gap:18px;align-items:center;padding:12px 28px;min-height:64px;box-shadow:0 2px 8px #0002}.topbar a{color:#dcecff;text-decoration:none}.topbar strong{letter-spacing:.035em;font-size:13px}.searchbar{display:flex;gap:6px;align-items:center;margin-left:auto}.searchbar input{width:210px;padding:9px 10px;border:1px solid #7890a8;border-radius:5px;font:inherit;font-size:13px}.topbar button{border:1px solid #7590ac;background:#183c61;color:white;border-radius:5px;padding:9px 12px;cursor:pointer;white-space:nowrap}.result{font-size:12px;min-width:55px;text-align:center}
.layout{display:grid;grid-template-columns:270px minmax(0,990px);gap:28px;max-width:1360px;margin:28px auto;padding:0 24px}.toc{align-self:start;position:sticky;top:92px;max-height:calc(100vh - 112px);overflow:auto;background:white;border:1px solid var(--line);border-radius:8px;padding:20px 17px}.toc h2{font-size:16px;margin:0 0 13px}.toc ol{list-style:none;margin:0;padding:0}.toc li{margin:0 0 8px;font-size:13px;line-height:1.65}.toc a{text-decoration:none;color:#445367;display:block;padding:4px;border-radius:4px}.toc a:hover,.toc a:focus{background:#eaf2fa;color:#0b4776}
article{background:white;border:1px solid var(--line);border-radius:8px;padding:50px 54px;min-width:0}.cover{border-bottom:3px solid #174f7d;padding-bottom:30px;margin-bottom:38px}.eyebrow{font-size:11px;letter-spacing:.16em;color:var(--blue);font-weight:bold}.cover h1{font-size:34px;line-height:1.45;letter-spacing:-.04em;margin:10px 0 16px}.subtitle{font-size:17px;color:#44596f;margin-bottom:20px}.metadata{display:flex;gap:10px;flex-wrap:wrap;color:var(--muted);font-size:12px}.metadata span{padding:4px 10px;background:#f1f5f9;border:1px solid #e2e8f0;border-radius:4px}
section{margin:0 0 42px}section h2{font-size:24px;line-height:1.55;letter-spacing:-.03em;margin:0 0 20px;border-bottom:1px solid var(--line);padding-bottom:12px;color:#133f64}h3{font-size:18px;margin:24px 0 10px;color:#164f7d}p{margin:0 0 17px;overflow-wrap:anywhere}ol.steps{padding-left:27px}ol.steps li{padding:2px 0 8px 7px}ul{padding-left:25px}li{margin-bottom:7px;overflow-wrap:anywhere}.note{border-left:4px solid #3b81ae;background:#edf6fc;border-radius:0 5px 5px 0;padding:16px 18px;margin:22px 0}.note strong{display:block;font-size:13px;margin-bottom:5px;color:#145079}.note.warning{background:#fff6e6;border-color:#c88a1f}.note.warning strong{color:#825700}.note p{margin:0}
.table-wrap{overflow:auto;margin:18px 0 26px}table{border-collapse:collapse;width:100%;font-size:13px;line-height:1.75}th{background:#eaf1f7;font-weight:700;text-align:left;color:#183e61}td,th{padding:12px 13px;border:1px solid #d5e0e9;vertical-align:top;overflow-wrap:anywhere}tbody tr:nth-child(even){background:#fafcfe}td:first-child{font-weight:600;min-width:110px}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f1f4f8;border:1px solid #dde5ee;border-radius:5px;padding:18px 20px;font:13px/1.85 Consolas,"Malgun Gothic",monospace;margin:18px 0 24px}.backtop{font-size:12px;color:#6c7d90;text-decoration:none}.document-footer{border-top:1px solid var(--line);padding-top:18px;font-size:12px;color:var(--muted)}mark{background:#fff098;color:inherit;padding:0 1px}mark.current{background:#ffc76d}.library{max-width:1150px;margin:42px auto;padding:0 24px}.library h1{font-size:34px;letter-spacing:-.035em}.cards{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:22px}.card{background:white;padding:27px;border:1px solid var(--line);border-radius:8px}.card h2{font-size:21px;margin:0 0 12px}.card p{font-size:14px;color:#52657a}.card ol{font-size:13px;color:#52657a}.card .open{display:inline-block;margin-top:12px;background:#164f7d;color:white;padding:8px 17px;border-radius:5px;text-decoration:none;font-size:14px}
@media(max-width:1050px){.layout{grid-template-columns:220px minmax(0,1fr);gap:16px;padding:0 14px}article{padding:30px 28px}.topbar{padding:10px 16px;flex-wrap:wrap}.searchbar{margin-left:0}.toc{top:130px}.cover h1{font-size:28px}}
@media(max-width:760px){.layout{display:block}.toc{position:static;max-height:none;margin-bottom:18px}.cards{grid-template-columns:1fr}.topbar strong{width:100%}.searchbar{flex-wrap:wrap}.searchbar input{width:160px}article{padding:25px 20px}.cover h1{font-size:26px}}
@page{size:A4;margin:18mm 16mm 18mm} @media print{body{background:white;font-size:10.5pt;line-height:1.7}.topbar,.toc,.backtop{display:none!important}.layout{display:block;max-width:none;padding:0;margin:0}article{border:0;border-radius:0;padding:0}.cover{padding-top:30mm;padding-bottom:20mm;min-height:240mm;break-after:page}.cover h1{font-size:30pt}.metadata span{background:none}.print-toc{display:block!important;break-after:page}.print-toc h2{font-size:20pt}.print-toc ol{padding-left:22px}.print-toc a{color:inherit;text-decoration:none}section{margin-bottom:26px}section h2{font-size:17pt;break-after:avoid}h3{font-size:12pt;break-after:avoid}p,li{orphans:3;widows:3}.note,pre,tr{break-inside:avoid}table{font-size:9pt}.table-wrap{overflow:visible}thead{display:table-header-group}th{background:#eef2f5!important;print-color-adjust:exact}td,th{padding:7px 8px}.note{print-color-adjust:exact}.document-footer{margin-top:25px}.library{margin:0;padding:0}.cards{display:block}.card{break-inside:avoid;margin-bottom:16px}}
.print-toc{display:none}
'''

SCRIPT = '''
const q=document.getElementById('manual-query'),article=document.querySelector('article');
let matches=[],current=-1,last='';
function updateHit(step){if(!matches.length)return;matches.forEach(n=>n.classList.remove('current'));current=(current+step+matches.length)%matches.length;matches[current].classList.add('current');matches[current].scrollIntoView({block:'center',behavior:'smooth'});document.getElementById('search-status').textContent=(current+1)+' / '+matches.length;}
function searchDoc(){const needle=q.value.trim();if(needle&&needle===last&&matches.length){updateHit(1);return;}article.querySelectorAll('mark').forEach(n=>n.replaceWith(document.createTextNode(n.textContent)));article.normalize();matches=[];current=-1;last=needle;const status=document.getElementById('search-status');if(!needle){status.textContent='';return;}const walker=document.createTreeWalker(article,NodeFilter.SHOW_TEXT);const nodes=[];let n;while(n=walker.nextNode()){if(!['SCRIPT','STYLE'].includes(n.parentElement.tagName)&&!n.parentElement.closest('.print-toc'))nodes.push(n);}for(const node of nodes){const value=node.nodeValue,lower=value.toLocaleLowerCase(),term=needle.toLocaleLowerCase();let start=0,pos=lower.indexOf(term);if(pos<0)continue;const fragment=document.createDocumentFragment();while(pos>=0){fragment.append(document.createTextNode(value.slice(start,pos)));const mark=document.createElement('mark');mark.textContent=value.slice(pos,pos+needle.length);fragment.append(mark);matches.push(mark);start=pos+needle.length;pos=lower.indexOf(term,start);}fragment.append(document.createTextNode(value.slice(start)));node.replaceWith(fragment);}status.textContent=matches.length?'': '결과 없음';updateHit(1);}
document.getElementById('search-form').addEventListener('submit',e=>{e.preventDefault();searchDoc();});document.getElementById('search-prev').addEventListener('click',()=>updateHit(-1));document.getElementById('search-next').addEventListener('click',()=>updateHit(1));document.addEventListener('keydown',e=>{if(e.key==='F3'){e.preventDefault();updateHit(e.shiftKey?-1:1);}});
'''


def block_html(block):
    escape = html.escape
    kind = block['type']
    if kind == 'paragraph':
        return '<p>' + escape(block['text']) + '</p>'
    if kind == 'heading':
        return '<h3>' + escape(block['text']) + '</h3>'
    if kind == 'code':
        return '<pre><code>' + escape(block['text']) + '</code></pre>'
    if kind == 'note':
        warning = block.get('level') == 'warning'
        return ('<aside class="note' + (' warning' if warning else '') + '"><strong>' +
                ('확인 사항' if warning else '참고') + '</strong><p>' + escape(block['text']) + '</p></aside>')
    if kind in {'steps', 'bullets'}:
        tag = 'ol' if kind == 'steps' else 'ul'
        return '<' + tag + (' class="steps"' if kind == 'steps' else '') + '>' + ''.join('<li>' + escape(item) + '</li>' for item in block['items']) + '</' + tag + '>'
    return ('<div class="table-wrap"><table><thead><tr>' +
            ''.join('<th scope="col">' + escape(str(heading)) + '</th>' for heading in block['headers']) +
            '</tr></thead><tbody>' + ''.join('<tr>' + ''.join('<td>' + escape(str(cell)) + '</td>' for cell in row) + '</tr>' for row in block['rows']) + '</tbody></table></div>')


def export_html():
    manuals = [load_manual(key) for key in TOOL_IDS]
    (MANUAL_DIR / 'manual.css').write_text(STYLE, encoding='utf-8')
    for manual in manuals:
        escape = html.escape
        toc = ''.join(f'<li><a href="#{escape(section["id"], quote=True)}">{escape(section["title"])}</a></li>' for section in manual['sections'])
        sections = ''.join('<section id="' + escape(section['id'], quote=True) + '"><h2>' + escape(section['title']) + '</h2>' +
                           ''.join(block_html(block) for block in section['blocks']) + '<a class="backtop" href="#document-top">목차와 처음으로 ↑</a></section>' for section in manual['sections'])
        page = ('<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                '<title>' + escape(manual['title']) + '</title><link rel="stylesheet" href="manual.css"></head><body id="document-top">'
                '<header class="topbar"><a href="index.html">설명서 목록</a><strong>BJT INTEGRATED WORKBENCH</strong>'
                '<form class="searchbar" id="search-form" role="search"><input id="manual-query" type="search" aria-label="설명서 본문 검색" placeholder="본문 검색"><button type="submit">검색</button><button type="button" id="search-prev" aria-label="이전 결과">↑</button><button type="button" id="search-next" aria-label="다음 결과">↓</button><output class="result" id="search-status" aria-live="polite"></output></form><button type="button" onclick="window.print()">인쇄 · PDF</button></header>'
                '<div class="layout"><nav class="toc" aria-label="목차"><h2>목차</h2><ol>' + toc + '</ol></nav><article><header class="cover">'
                '<div class="eyebrow">USER MANUAL · BJT 통합 작업 도구</div><h1>' + escape(manual['title']) + '</h1><p class="subtitle">' + escape(manual.get('subtitle', '')) + '</p>'
                '<div class="metadata"><span>문서 ' + escape(manual.get('document_version', '1.1')) + '</span><span>' + escape(manual.get('document_date', '2026.10.21').replace('-', '.')) + '</span><span>한국어 · 오프라인판</span></div></header>'
                '<nav class="print-toc" aria-label="인쇄용 목차"><h2>목차</h2><ol>' + toc + '</ol></nav>' + sections +
                '<footer class="document-footer">BJT 통합 작업 도구 · ' + escape(manual['title']) + ' · 문서 ' + escape(manual.get('document_version', '1.1')) + '</footer></article></div><script>' + SCRIPT + '</script></body></html>')
        (MANUAL_DIR / (manual['id'] + '.html')).write_text(page, encoding='utf-8')
    cards = ''.join('<section class="card"><h2>' + html.escape(m['title']) + '</h2><p>' + html.escape(m.get('subtitle', '')) + '</p><p>목차 ' + str(len(m['sections'])) + '개 장 · 문서 ' + html.escape(m.get('document_version', '1.1')) + '</p><ol>' +
                    ''.join('<li>' + html.escape(s['title'].split('. ', 1)[-1]) + '</li>' for s in m['sections'][:5]) +
                    '</ol><a class="open" href="' + m['id'] + '.html">사용설명서 열기 →</a></section>' for m in manuals)
    index = ('<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>BJT 통합 작업 도구 · 사용설명서</title><link rel="stylesheet" href="manual.css"></head><body><header class="topbar"><strong>BJT INTEGRATED WORKBENCH · 사용설명서</strong></header><main class="library"><div class="eyebrow">DOCUMENTATION CENTER</div><h1>도구별 사용설명서</h1><p>목차, 단계별 절차, 설정값, 작업 예시와 문제 해결을 도구별로 확인하세요.<br>프로그램의 각 탭 상단 ‘사용설명서’ 또는 메인 창의 F1로도 열 수 있습니다.</p><div class="cards">' + cards + '</div><p class="document-footer">통합판 1.6.2 · 2026.10.26 · 모든 설명서는 오프라인 열람과 브라우저 인쇄를 지원합니다.</p></main></body></html>')
    (MANUAL_DIR / 'index.html').write_text(index, encoding='utf-8')
    return [{'id': m['id'], 'chapters': len(m['sections']), 'characters': len(manual_plain_text(m))} for m in manuals]


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='BJT offline user manual exporter')
    parser.add_argument('--build', action='store_true')
    args = parser.parse_args()
    if args.build:
        print(json.dumps(export_html(), ensure_ascii=False, indent=2))
