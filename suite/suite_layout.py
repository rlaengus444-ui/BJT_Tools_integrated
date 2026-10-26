"""Presentation-only layout adaptations for narrow integrated windows."""
from tkinter import ttk


def adapt_tool_layout(host, key, controller):
    if key != 'arrange' or getattr(controller, '_suite_responsive_layout', False):
        return
    children = host.winfo_children()
    top = children[0]
    controls = top.winfo_children()
    # Match the preserved toolbar before changing its packing. Inputs and
    # their original widgets, commands and bindings remain the same objects.
    if len(controls) != 17 or controls[0].cget('text') != '최종 해상도':
        return
    original = [{key: value for key, value in widget.pack_info().items() if key != 'in'}
                for widget in controls]
    rows = (ttk.Frame(top), ttk.Frame(top))
    state = {'compact': None}

    def resize(event=None):
        width = event.width if event is not None else host.winfo_width()
        compact = width < 1380
        if compact == state['compact']:
            return
        state['compact'] = compact
        for widget in controls:
            widget.pack_forget()
        for row in rows:
            row.pack_forget()
        if compact:
            rows[0].pack(fill='x')
            rows[1].pack(fill='x', pady=(5, 0))
        for index, (widget, options) in enumerate(zip(controls, original)):
            parent = rows[0 if index <= 10 else 1] if compact else top
            widget.pack(in_=parent, **options)
            # These containers were created after the preserved widgets.
            # Raise siblings above them so native Tk actually paints controls.
            widget.lift()

    # Reserve the output controls before the expanding preview pane consumes
    # the vertical cavity. This keeps export/cancel accessible on short screens.
    pane = next((item for item in children if isinstance(item, ttk.Panedwindow)), None)
    output = controller.export_button.master
    if pane is not None:
        output.pack_configure(side='bottom', before=pane)
        left = controller.placement_list.master.master.master
        groups = left.winfo_children()
        expected = ('이미지 세트 / STL 배치', '선택 배치', '미리보기', '조작')
        if len(groups) == 4 and tuple(item.cget('text') for item in groups) == expected:
            # Each original group stays intact in a compact notebook. Using
            # pack's descendant container preserves widget identities and
            # every original binding, including preview-wheel handling.
            for group in groups:
                group.pack_forget()
            notebook = ttk.Notebook(left)
            notebook.pack(fill='both', expand=True)
            pages = [ttk.Frame(notebook, padding=3) for _ in range(3)]
            for page, title in zip(pages, ('파일·배치', '미리보기', '조작 안내')):
                notebook.add(page, text=title)
            groups[1].pack(in_=pages[0], side='bottom', fill='x', pady=(7, 0))
            groups[0].pack(in_=pages[0], fill='both', expand=True)
            source_children = groups[0].winfo_children()
            # Reserve add/copy/delete actions before the expanding file list.
            if len(source_children) == 3:
                source_children[2].pack_configure(side='bottom', before=source_children[1])
            groups[2].pack(in_=pages[1], fill='x')
            groups[3].pack(in_=pages[2], fill='x')
            for group in groups:
                group.lift()
            controller._suite_side_notebook = notebook
    host.bind('<Configure>', resize, add='+')
    host.after(0, resize)
    controller._suite_responsive_layout = True
    controller._suite_toolbar_controls = controls
    controller._suite_toolbar_rows = rows
