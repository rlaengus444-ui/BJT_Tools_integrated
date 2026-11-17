"""Shared presentation layer for the BJT tool interfaces.

Only visual widget options are adjusted. Commands, states, variables, geometry,
bindings, canvases, image previews and scientific plots remain with each tool.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont, ttk


COLORS = {
    'background': '#f3f6fb',
    'surface': '#ffffff',
    'ink': '#172238',
    'muted': '#5e6d84',
    'primary': '#315efb',
    'primary_hover': '#244bd6',
    'primary_pressed': '#1f40b8',
    'navy': '#111c32',
    'navy_light': '#21324e',
    'header_text': '#ffffff',
    'header_muted': '#c0cde0',
    'border': '#d8e1ee',
    'hover': '#eaf0fb',
    'selection': '#dce7ff',
    'selection_ink': '#193b78',
    'disabled': '#e8edf4',
    'disabled_ink': '#64748b',
    'success': '#16734d',
    'success_background': '#e8f5ed',
    'warning': '#925600',
    'warning_background': '#fff4dc',
    'danger': '#b32d46',
    'danger_hover': '#96263b',
    'danger_background': '#fff0f3',
}

FONTS = {
    'body': ('맑은 고딕', 9),
    'text': ('맑은 고딕', 10),
    'small': ('맑은 고딕', 9),
    'bold': ('맑은 고딕', 9, 'bold'),
    'heading': ('맑은 고딕', 10, 'bold'),
    'title': ('맑은 고딕', 18, 'bold'),
    'brand': ('Segoe UI', 16, 'bold'),
    'hero': ('맑은 고딕', 19, 'bold'),
    'card_title': ('맑은 고딕', 11, 'bold'),
    'mono': ('Consolas', 10),
}

THEME_NAME = 'BjtSuite'

_BUTTON_ROLES = {
    'bjt': {'primary': ('run_button',), 'danger': ('cancel_button',)},
    'arrange': {'primary': ('export_button',), 'danger': ('cancel_button',)},
    'overlay': {'primary': ('run_button',), 'danger': ('cancel_button',)},
    'exchanger': {'primary': ('run_btn',)},
    'alignment': {'primary': ('run_button',), 'danger': ('stop_button',)},
    'thermal': {'primary': ('run_btn', 'fit_btn'), 'danger': ('stop_btn',)},
    'compare': {'primary': ('run_btn',), 'danger': ('cancel_btn',)},
    'number_tools': {'primary': ('duplicate_button', 'scan_button'), 'danger': ('stop_button',)},
    'number': {'primary': ('duplicate_button', 'scan_button'), 'danger': ('stop_button',)},
    'sequence_z': {'primary': ('calculate_button',), 'danger': ('stop_button',)},
    'm2_converter': {'primary': ('run_button',), 'danger': ('stop_button',)},
    'nesting': {'primary': ('run_button',), 'danger': ('stop_button',)},
    'ir_feedback': {'primary': ('connect_button',), 'danger': ('disconnect_button',)},
}


class ThemeManager:
    def __init__(self, root):
        self.root = root
        self.style = ttk.Style(root)
        self._defaults_applied = False

    def _defaults(self):
        if self._defaults_applied:
            return
        # Existing named fonts are updated once; no Tk font is created on
        # subsequent mount/tab refreshes. Explicit original heading fonts stay.
        for name in ('TkDefaultFont', 'TkTextFont', 'TkMenuFont', 'TkTooltipFont'):
            try:
                tkfont.nametofont(name, root=self.root).configure(family=FONTS['body'][0], size=9)
            except tk.TclError:
                pass
        try:
            tkfont.nametofont('TkHeadingFont', root=self.root).configure(family=FONTS['body'][0], size=9, weight='bold')
        except tk.TclError:
            pass
        # Combobox popdown listboxes are not children in normal traversal.
        for suffix, value in {
            'background': COLORS['surface'], 'foreground': COLORS['ink'],
            'selectBackground': COLORS['selection'], 'selectForeground': COLORS['selection_ink'],
            'font': FONTS['body'],
        }.items():
            self.root.option_add('*TCombobox*Listbox.' + suffix, value, 20)
        self._defaults_applied = True

    def apply(self):
        """Idempotently restore one theme after an original tool selected one."""
        style, c = self.style, COLORS
        if THEME_NAME not in style.theme_names():
            style.theme_create(THEME_NAME, parent='clam')
        if style.theme_use() != THEME_NAME:
            style.theme_use(THEME_NAME)
        self._defaults()
        style.configure('.', font=FONTS['body'], background=c['background'], foreground=c['ink'],
                        bordercolor=c['border'], darkcolor=c['border'], lightcolor=c['surface'],
                        troughcolor=c['disabled'], selectbackground=c['selection'], selectforeground=c['selection_ink'])
        style.configure('TFrame', background=c['background'])
        style.configure('TLabel', background=c['background'], foreground=c['ink'], font=FONTS['body'])
        style.configure('TLabelframe', background=c['background'], bordercolor=c['border'], borderwidth=1, relief='solid')
        style.configure('TLabelframe.Label', background=c['background'], foreground=c['ink'], font=FONTS['bold'])
        self._button('TButton', c['surface'], c['ink'], c['hover'], c['selection'], padding=(9, 4))
        self._button('Suite.Secondary.TButton', c['surface'], c['ink'], c['hover'], c['selection'])
        self._button('Suite.Primary.TButton', c['primary'], '#ffffff', c['primary_hover'], c['primary_pressed'], bold=True)
        self._button('Suite.Danger.TButton', c['danger_background'], c['danger'], '#fce1e7', '#f8d2db')
        self._button('Suite.Header.TButton', c['navy_light'], '#ffffff', '#2c4266', '#354e77',
                     border='#3c506e', disabled_background='#26374f', disabled_foreground='#acb9cc')
        for name in ('TCheckbutton', 'TRadiobutton'):
            style.configure(name, background=c['background'], foreground=c['ink'], font=FONTS['body'],
                            indicatorbackground=c['surface'], indicatorforeground=c['primary'], focuscolor=c['primary'])
            style.map(name, background=[('active', c['background'])],
                      foreground=[('disabled', c['disabled_ink']), ('!disabled', c['ink'])],
                      indicatorbackground=[('disabled', c['disabled']), ('selected', c['primary'])])
        for name in ('TEntry', 'TCombobox', 'TSpinbox'):
            style.configure(name, fieldbackground=c['surface'], background=c['surface'], foreground=c['ink'],
                            bordercolor=c['border'], lightcolor=c['surface'], darkcolor=c['surface'],
                            insertcolor=c['ink'], arrowcolor=c['muted'], padding=(5, 3), font=FONTS['body'])
            style.map(name, fieldbackground=[('disabled', c['disabled']), ('readonly', c['surface'])],
                      foreground=[('disabled', c['disabled_ink']), ('!disabled', c['ink'])],
                      bordercolor=[('invalid', c['danger']), ('focus', c['primary']), ('!focus', c['border'])],
                      selectbackground=[('!disabled', c['selection'])],
                      selectforeground=[('!disabled', c['selection_ink'])])
        style.configure('TNotebook', background=c['background'], borderwidth=0, tabmargins=(0, 4, 0, 0))
        style.configure('TNotebook.Tab', background='#e8eef7', foreground=c['muted'],
                        padding=(11, 5), font=FONTS['body'], bordercolor=c['border'])
        style.map('TNotebook.Tab', background=[('selected', c['surface']), ('active', c['hover'])],
                  foreground=[('disabled', c['disabled_ink']), ('selected', c['ink']), ('!disabled', c['muted'])])
        style.configure('Treeview', background=c['surface'], fieldbackground=c['surface'], foreground=c['ink'],
                        rowheight=26, borderwidth=1, relief='solid', bordercolor=c['border'], font=FONTS['body'])
        style.configure('Treeview.Heading', background='#eaf0f8', foreground=c['ink'],
                        font=FONTS['bold'], padding=(7, 5), relief='flat')
        style.map('Treeview', background=[('selected', c['selection'])],
                  foreground=[('disabled', c['disabled_ink']), ('selected', c['selection_ink'])])
        style.map('Treeview.Heading', background=[('active', '#dfe8f6')], foreground=[('!disabled', c['ink'])])
        for orientation in ('Horizontal', 'Vertical'):
            style.configure(orientation + '.TProgressbar', background=c['primary'], troughcolor=c['disabled'],
                            bordercolor=c['disabled'], lightcolor=c['primary'], darkcolor=c['primary'], thickness=8)
            style.configure(orientation + '.TScrollbar', background='#c0ccdc', troughcolor=c['background'],
                            bordercolor=c['background'], lightcolor='#c0ccdc', darkcolor='#c0ccdc', arrowcolor=c['muted'])
            style.map(orientation + '.TScrollbar', background=[('active', '#9dafc6'), ('pressed', '#8399b6')])
        style.configure('TScale', background=c['background'], troughcolor=c['disabled'], bordercolor=c['border'])
        style.configure('TSeparator', background=c['border'])
        self._semantic_styles()
        self._compare_aliases()
        return self

    def _button(self, name, background, foreground, hover, pressed, *, padding=(10, 5), bold=False,
                border=None, disabled_background=None, disabled_foreground=None):
        c, style = COLORS, self.style
        style.configure(name, background=background, foreground=foreground, bordercolor=border or c['border'],
                        lightcolor=background, darkcolor=background, focuscolor=c['primary'],
                        padding=padding, font=FONTS['bold'] if bold else FONTS['body'], relief='flat', borderwidth=1)
        style.map(name,
                  background=[('disabled', disabled_background or c['disabled']), ('pressed', pressed), ('active', hover), ('!disabled', background)],
                  foreground=[('disabled', disabled_foreground or c['disabled_ink']), ('!disabled', foreground)],
                  bordercolor=[('disabled', c['border']), ('focus', c['primary']), ('!focus', border or c['border'])],
                  lightcolor=[('disabled', disabled_background or c['disabled']), ('!disabled', background)],
                  darkcolor=[('disabled', disabled_background or c['disabled']), ('!disabled', background)])

    def _semantic_styles(self):
        c, s = COLORS, self.style
        for name, color in {
            'Suite.Header.TFrame': c['navy'], 'Suite.Hero.TFrame': c['navy'],
            'Suite.Tool.TFrame': c['background'], 'Suite.Card.TFrame': c['surface'],
            'Suite.WhiteCard.TFrame': c['surface'], 'Suite.Surface.TFrame': c['surface'],
        }.items():
            s.configure(name, background=color)
        for name, background, foreground, font in (
            ('Suite.Brand.TLabel', c['navy'], '#ffffff', FONTS['brand']),
            ('Suite.HeaderMeta.TLabel', c['navy'], c['header_muted'], FONTS['small']),
            ('Suite.HeroTitle.TLabel', c['navy'], '#ffffff', FONTS['hero']),
            ('Suite.HeroMeta.TLabel', c['navy'], c['header_muted'], FONTS['text']),
            ('Suite.CardTitle.TLabel', c['surface'], c['ink'], FONTS['card_title']),
            ('Suite.CardText.TLabel', c['surface'], c['muted'], FONTS['body']),
            ('Suite.Muted.TLabel', c['background'], c['muted'], FONTS['small']),
            ('Suite.Subtitle.TLabel', c['background'], c['muted'], FONTS['text']),
            ('Suite.Title.TLabel', c['background'], c['ink'], FONTS['title']),
            ('Suite.IR.Metric.TLabel', c['background'], c['ink'], (FONTS['body'][0], 22, 'bold')),
            ('Suite.IR.Status.TLabel', c['background'], c['ink'], FONTS['heading']),
            ('Suite.Status.TLabel', c['surface'], c['muted'], FONTS['small']),
            ('Suite.Status.Success.TLabel', c['success_background'], c['success'], FONTS['bold']),
            ('Suite.Status.Warning.TLabel', c['warning_background'], c['warning'], FONTS['bold']),
            ('Suite.Status.Error.TLabel', c['danger_background'], c['danger'], FONTS['bold']),
        ):
            s.configure(name, background=background, foreground=foreground, font=font)
        s.configure('Suite.Card.TLabelframe', background=c['surface'], bordercolor=c['border'], relief='solid', borderwidth=1)
        s.configure('Suite.Card.TLabelframe.Label', background=c['surface'], foreground=c['ink'], font=FONTS['card_title'])
        s.configure('Suite.Nav.TNotebook', background=c['background'], borderwidth=0, tabmargins=(0, 2, 0, 3))
        s.configure('Suite.Nav.TNotebook.Tab', background='#e6edf8', foreground=c['muted'], font=FONTS['body'], padding=(6, 6))
        s.map('Suite.Nav.TNotebook.Tab', background=[('selected', c['primary']), ('active', '#dbe5f8')],
              foreground=[('disabled', c['disabled_ink']), ('selected', '#ffffff'), ('!disabled', c['muted'])])

    def _compare_aliases(self):
        c, s = COLORS, self.style
        s.configure('SliceCompare.TLabel', background=c['background'], foreground=c['ink'], font=FONTS['body'])
        s.configure('SliceCompare.TFrame', background=c['background'])
        self._button('SliceCompare.TButton', c['surface'], c['ink'], c['hover'], c['selection'])
        self._button('SliceCompare.Accent.TButton', c['primary'], '#ffffff', c['primary_hover'], c['primary_pressed'], bold=True)
        s.configure('SliceCompare.Treeview', background=c['surface'], fieldbackground=c['surface'], foreground=c['ink'],
                    rowheight=28, font=FONTS['body'], bordercolor=c['border'], borderwidth=1)
        s.configure('SliceCompare.Treeview.Heading', background='#eaf0f8', foreground=c['ink'], font=FONTS['bold'], padding=(7, 5))
        s.map('SliceCompare.Treeview', background=[('selected', c['selection'])], foreground=[('selected', c['selection_ink'])])
        s.configure('SliceCompare.TNotebook', background=c['background'], borderwidth=0)
        s.configure('SliceCompare.TNotebook.Tab', padding=(11, 5), font=FONTS['body'])

    def style_tool(self, host, key, controller):
        self.apply()
        self._walk_classic(host)
        roles = _BUTTON_ROLES.get(key, {})
        for role, attributes in roles.items():
            name = 'Suite.Primary.TButton' if role == 'primary' else 'Suite.Danger.TButton'
            for attribute in attributes:
                widget = getattr(controller, attribute, None)
                try:
                    if widget is not None and widget.winfo_class() == 'TButton':
                        widget.configure(style=name)
                except tk.TclError:
                    pass
        return self

    def style_window(self, toplevel):
        self.apply()
        self._walk_classic(toplevel)
        return self

    def _walk_classic(self, widget):
        try:
            # Never recolor a canvas: these include slice/layout previews,
            # Matplotlib renderers, data overlays and interaction surfaces.
            if widget.winfo_class() != 'Canvas':
                self._classic(widget)
            for child in widget.winfo_children():
                self._walk_classic(child)
        except tk.TclError:
            return

    @staticmethod
    def _configure_visual(widget, options):
        available = set(widget.keys())
        selected = {key: value for key, value in options.items() if key in available}
        if selected:
            widget.configure(**selected)

    @staticmethod
    def _parent_background(widget):
        parent = getattr(widget, 'master', None)
        if parent is not None:
            try:
                if 'background' in parent.keys():
                    return str(parent.cget('background'))
                style_name = parent.cget('style') or parent.winfo_class()
                return ttk.Style(parent).lookup(style_name, 'background') or COLORS['background']
            except tk.TclError:
                pass
        return COLORS['background']

    def _classic(self, widget):
        name, c = widget.winfo_class(), COLORS
        if name in ('Text', 'Listbox'):
            options = {
                'background': c['surface'], 'foreground': c['ink'],
                'selectbackground': c['selection'], 'selectforeground': c['selection_ink'],
                'inactiveselectbackground': '#e8eef8', 'insertbackground': c['ink'],
                'highlightbackground': c['border'], 'highlightcolor': c['primary'],
                'highlightthickness': 1, 'borderwidth': 0, 'relief': 'flat',
            }
            if str(widget.cget('font')) in {'TkFixedFont', 'TkDefaultFont', 'TkTextFont'}:
                options['font'] = FONTS['text']
            self._configure_visual(widget, options)
            return
        if name not in ('Frame', 'Label', 'Labelframe', 'Toplevel', 'Tk'):
            return
        if 'background' not in widget.keys():
            return
        background = str(widget.cget('background'))
        neutral = background.lower()
        if neutral in ('white', '#fff', '#ffffff', 'systemwindow'):
            new_background = c['surface']
        elif neutral in ('systembuttonface', '#f0f0f0', '#d9d9d9', '#ececec', '#f5f5f5', '#eeeeee', '#f3f5f8', '#f3f6fb'):
            new_background = self._parent_background(widget)
            if new_background.lower() in ('systembuttonface', '#f0f0f0', '#d9d9d9'):
                new_background = c['background']
        else:
            new_background = None
        options = {'background': new_background} if new_background is not None else {}
        if name == 'Label' and 'foreground' in widget.keys():
            foreground = str(widget.cget('foreground')).lower()
            if foreground in ('systemwindowtext', 'systembuttontext', 'black', '#000', '#000000', '#172238'):
                options['foreground'] = c['ink']
            elif foreground in ('#555', '#555555', '#666', '#666666', '#63738a', '#5b6779', 'gray', 'grey'):
                options['foreground'] = c['muted']
        # In particular, never change label text/images/variables, explicit
        # heading fonts, dimensions, geometry managers, state or bindings.
        self._configure_visual(widget, options)
