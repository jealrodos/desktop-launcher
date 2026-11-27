"""Personal Windows dock. Python/Tk standard library only; no network access."""
import argparse
import ctypes
from ctypes import wintypes as W
import datetime as dt
import json
import logging
import os
import re
from pathlib import Path
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import uuid
import winreg
import windows_app

APP_DIR = Path(os.environ['APPDATA']) / 'DesktopDock'
CONFIG = APP_DIR / 'config.json'
PYTHONW = str(Path(sys.executable).with_name('pythonw.exe'))
SCRIPT = str(Path(__file__).resolve())
RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
GROUPS=('Daily','Create','Build','Play','Tools')
user32, kernel32, shell32 = ctypes.windll.user32, ctypes.windll.kernel32, ctypes.windll.shell32
user32.SetProcessDPIAware()
APP_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(filename=APP_DIR / 'python-errors.log', level=logging.ERROR)
shell32.ShellExecuteW.argtypes = [W.HWND, W.LPCWSTR, W.LPCWSTR, W.LPCWSTR, W.LPCWSTR, ctypes.c_int]
shell32.ShellExecuteW.restype = ctypes.c_ssize_t
kernel32.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
kernel32.OpenProcess.restype = W.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [W.HANDLE, W.DWORD, W.LPWSTR, ctypes.POINTER(W.DWORD)]
kernel32.CloseHandle.argtypes = [W.HANDLE]
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, W.BOOL, W.LPCWSTR]
kernel32.CreateMutexW.restype = W.HANDLE
user32.GetWindowThreadProcessId.argtypes = [W.HWND, ctypes.POINTER(W.DWORD)]
user32.IsWindowVisible.argtypes = [W.HWND]
user32.GetWindowTextLengthW.argtypes = [W.HWND]
user32.SetForegroundWindow.argtypes = [W.HWND]
user32.ShowWindow.argtypes = [W.HWND, ctypes.c_int]
user32.IsIconic.argtypes = [W.HWND]
user32.GetParent.argtypes = [W.HWND]
user32.GetParent.restype = W.HWND
user32.FindWindowExW.argtypes = [W.HWND,W.HWND,W.LPCWSTR,W.LPCWSTR]
user32.FindWindowExW.restype = W.HWND
user32.SetParent.argtypes = [W.HWND,W.HWND]
user32.SetParent.restype = W.HWND
user32.GetWindowLongW.argtypes = [W.HWND,ctypes.c_int]
user32.GetWindowLongW.restype = W.LONG
user32.SetWindowLongW.argtypes = [W.HWND,ctypes.c_int,W.LONG]
user32.GetWindowRect.argtypes = [W.HWND,ctypes.POINTER(W.RECT)]
user32.SetWindowPos.argtypes = [W.HWND,W.HWND,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,W.UINT]


class GUID(ctypes.Structure):
    _fields_ = [('Data1', W.DWORD), ('Data2', W.WORD), ('Data3', W.WORD), ('Data4', W.BYTE * 8)]


class PowerStatus(ctypes.Structure):
    _fields_ = [('ACLineStatus', W.BYTE), ('BatteryFlag', W.BYTE),
                ('BatteryLifePercent', W.BYTE), ('SystemStatusFlag', W.BYTE),
                ('BatteryLifeTime', W.DWORD), ('BatteryFullLifeTime', W.DWORD)]


POWER_MODES = {
    'balanced': ('00000000-0000-0000-0000-000000000000', 'Balanced'),
    'performance': ('ded574b5-45a0-4f42-8737-46345c09c238', 'Performance'),
}


def power_api():
    library = ctypes.WinDLL('powrprof.dll')
    for name in ('PowerGetEffectiveOverlayScheme', 'PowerSetActiveOverlayScheme'):
        function = getattr(library, name)
        function.argtypes = [ctypes.POINTER(GUID)]
        function.restype = W.DWORD
    return library


def read_power_mode():
    guid = GUID()
    code = power_api().PowerGetEffectiveOverlayScheme(ctypes.byref(guid))
    if code:
        raise ctypes.WinError(code)
    value = str(uuid.UUID(bytes_le=bytes(guid)))
    mode, label = 'other', 'Other mode'
    for key, (identifier, name) in POWER_MODES.items():
        if value == identifier:
            mode, label = key, name
            break
    if value == '961cc777-2547-4f9d-8174-7d86181b8a7a':
        label = 'Efficiency'
    status = PowerStatus()
    if not kernel32.GetSystemPowerStatus(ctypes.byref(status)):
        raise ctypes.WinError()
    return {'Mode': mode, 'Label': label, 'Guid': value,
            'PowerSource': {0: 'On battery', 1: 'Plugged in'}.get(status.ACLineStatus, 'Unknown'),
            'Available': True}


def set_power_mode(mode):
    if mode not in POWER_MODES:
        raise ValueError('Choose Balanced or Performance.')
    before = read_power_mode()
    guid = GUID.from_buffer_copy(uuid.UUID(POWER_MODES[mode][0]).bytes_le)
    code = power_api().PowerSetActiveOverlayScheme(ctypes.byref(guid))
    if code:
        raise ctypes.WinError(code)
    actual = read_power_mode()
    if actual['Guid'] != POWER_MODES[mode][0]:
        raise RuntimeError('Windows did not retain that power mode. Check Windows power settings.')
    try:
        record = {'Time': dt.datetime.now().isoformat(), 'Before': before, 'After': actual}
        with (APP_DIR / 'power-mode-history.jsonl').open('a', encoding='utf-8') as history:
            history.write(json.dumps(record) + '\n')
    except OSError:
        logging.exception('Saving power-mode change history')
    return actual


def startup_command():
    return f'"{PYTHONW}" "{SCRIPT}" --app'


def startup_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            return winreg.QueryValueEx(key, 'DesktopDock')[0] == startup_command()
    except OSError:
        return False


def set_startup(enabled):
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        if enabled:
            winreg.SetValueEx(key, 'DesktopDock', 0, winreg.REG_SZ, startup_command())
        else:
            try:
                winreg.DeleteValue(key, 'DesktopDock')
            except FileNotFoundError:
                pass


def save_config(config):
    temporary = CONFIG.with_suffix('.tmp')
    temporary.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding='utf-8')
    if CONFIG.exists():
        CONFIG.with_suffix('.backup.json').write_bytes(CONFIG.read_bytes())
    os.replace(temporary, CONFIG)


def load_config():
    config = json.loads(CONFIG.read_text(encoding='utf-8-sig'))
    if not isinstance(config.get('Items'), list):
        raise ValueError('Invalid dock configuration: Items must be a list')
    ids = [item['Id'] for item in config['Items']]
    if any(not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',identifier) for identifier in ids):
        raise ValueError('Shortcut IDs may contain only letters, digits, hyphens and underscores')
    if len(ids) != len(set(ids)):
        raise ValueError('Each dock shortcut must have a unique Id')
    if config.get('SelectedGroup') not in GROUPS:
        config['SelectedGroup'] = 'Create'
    config['IconSize'] = min((32, 40, 48, 56, 64, 72), key=lambda value: abs(value - config.get('IconSize', 64)))
    return config


def open_target(item):
    target = os.path.expandvars(item['Target'])
    working = item.get('WorkingDirectory') or None
    if working and not Path(working).is_dir():
        working = None
    if target.lower().startswith('shell:'):
        result = shell32.ShellExecuteW(None, 'open', 'explorer.exe', target, working, 1)
    else:
        result = shell32.ShellExecuteW(None, 'open', target, item.get('Arguments') or None, working, 1)
    if result <= 32:
        raise OSError(f'Windows could not open this shortcut (error {result}).')


def app_windows():
    """Enumerate visible app windows, without reading their titles or contents."""
    windows = {}
    callback_type = ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
    def visit(hwnd, _):
        if not user32.IsWindowVisible(hwnd) or not user32.GetWindowTextLengthW(hwnd):
            return True
        pid = W.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        handle = kernel32.OpenProcess(0x1000, False, pid.value)
        if handle:
            try:
                text = ctypes.create_unicode_buffer(32768)
                count = W.DWORD(len(text))
                if kernel32.QueryFullProcessImageNameW(handle, 0, text, ctypes.byref(count)):
                    windows.setdefault(Path(text.value).stem.lower(), []).append(hwnd)
            finally:
                kernel32.CloseHandle(handle)
        return True
    callback = callback_type(visit)
    user32.EnumWindows(callback, 0)
    return windows


def work_area():
    area = W.RECT()
    if not user32.SystemParametersInfoW(48, 0, ctypes.byref(area), 0):
        raise ctypes.WinError()
    return area.left, area.top, area.right, area.bottom


class Dock:
    def __init__(self, config, preview=False, app=False):
        self.config, self.preview, self.app = config, preview, app
        if app:windows_app.configure_process_identity()
        self.root = tk.Tk(className='DesktopDock')
        if app:self.root.withdraw()
        self.root.title('Desktop Launcher')
        if (APP_DIR/'DesktopDock.ico').exists():
            self.root.iconbitmap(str(APP_DIR/'DesktopDock.ico'))
        self.root.report_callback_exception = self.error
        self.root.resizable(app, app)
        self.root.attributes('-topmost',False if app else preview or not config.get('DesktopMode',False))
        self.root.configure(bg='#00242d' if app else '#ff00ff')
        if not app:self.root.attributes('-transparentcolor', '#ff00ff')
        self.root.overrideredirect(False if app else not preview)
        self.scale = self.root.winfo_fpixels('1i') / 96
        if app:
            self.app_group=config['SelectedGroup']
            self.app_render_job=None
            self.build_app_widgets()
        else:
            self.canvas = tk.Canvas(self.root, bg='#ff00ff', highlightthickness=0)
            self.canvas.pack()
        self.images, self.buttons = [], {}
        self.windows, self.modal, self.dragged = {}, False, False
        self.peek_active=False
        self.peek_job=None
        self.peek_reason='command'
        self.edge_entered=None
        self.edge_armed=True
        self.edge_cooldown=0
        self.power_state={}
        self.last_inside, self.hidden, self.y = dt.datetime.now(), False, 0
        self.canvas.bind('<Button-3>', self.background_menu)
        self.root.bind('<Escape>', lambda event:self.search_text.set('') if self.app else self.finish_peek(force=True))
        self.root.bind('<Control-q>', lambda event: self.root.destroy())
        self.hotkey = False
        self.hotkey_thread_id = None
        self.hotkey_ready = threading.Event()
        self.hotkey_thread = threading.Thread(target=self.hotkey_loop, daemon=True)
        self.hotkey_thread.start()
        self.root.protocol('WM_DELETE_WINDOW', self.root.destroy)
        self.desktop_host=None
        self.render()
        if app:
            self.root.update_idletasks()
            hwnd=self.wrapper_handle()
            # This is our own top-level window, with its own taskbar button.
            style=user32.GetWindowLongW(hwnd,-20)
            user32.SetWindowLongW(hwnd,-20,(style|0x40000)&~0x80)
            windows_app.set_properties(windows_app.app_properties(SCRIPT,APP_DIR/'DesktopDock.ico'),hwnd=hwnd)
            if not preview:self.root.deiconify()
            self.root.bind('<Configure>',self.app_resized)
            self.root.bind('<Control-f>',lambda event:self.focus_search())
            self.root.bind('<Map>',lambda event:self.root.after_idle(self.write_runtime) if event.widget==self.root else None)
            self.root.bind('<Unmap>',lambda event:self.root.after_idle(self.write_runtime) if event.widget==self.root else None)
            self.root.after_idle(self.write_runtime)
        elif self.config.get('DesktopMode',False) and not preview:
            self.root.after_idle(self.attach_to_desktop)
        self.update_running()
        if not app:self.root.after(250, self.poll_hide)
        self.root.after(2000, self.poll_display)
        if not app:self.root.after(100,self.poll_edge)

    def s(self, number):
        return round(number * self.scale)

    def build_app_widgets(self):
        left,top,right,bottom=work_area()
        width=min(self.s(850),right-left-self.s(40))
        height=min(self.s(550),bottom-top-self.s(70))
        self.root.geometry(f'{width}x{height}+{left+(right-left-width)//2}+{top+(bottom-top-height)//2}')
        self.root.minsize(min(self.s(680),width),min(self.s(400),height))
        self.app_frames=[]
        self.app_labels=[]
        def frame(parent,**pack):
            widget=tk.Frame(parent,bg='#00242d')
            widget.pack(**pack)
            self.app_frames.append(widget)
            return widget
        header=frame(self.root,fill='x',padx=self.s(24),pady=(self.s(22),self.s(12)))
        title_frame=frame(header,side='left')
        self.app_wordmark_labels=[]
        for text,family in (('desktop','Segoe UI Semibold'),('launcher','Segoe UI Light')):
            label=tk.Label(title_frame,text=text,font=(family,18),bg='#00242d',fg='#eee8d5',padx=0)
            label.pack(side='left')
            self.app_wordmark_labels.append(label)
            self.app_labels.append(label)
        # Match the shared right-aligned parent signature in the web headers.
        atria_font=Path(SCRIPT).with_name('atria-wordmark.ttf')
        self.atria_font='Segoe UI'
        add_font=ctypes.windll.gdi32.AddFontResourceExW
        add_font.argtypes=[W.LPCWSTR,W.DWORD,ctypes.c_void_p]
        add_font.restype=ctypes.c_int
        if atria_font.exists() and add_font(str(atria_font),0x10,None):
            self.atria_font='Josefin Sans'
        self.atria_signature=tk.Canvas(header,width=self.s(175),height=self.s(30),highlightthickness=0)
        self.atria_signature.pack(side='right',padx=(self.s(12),0))
        self.power_button=tk.Button(header,text='Power mode',command=self.toggle_power_mode,font=('Segoe UI',10,'bold'),relief='flat',bd=0,padx=self.s(18),pady=self.s(8),cursor='hand2')
        self.power_button.pack(side='right')
        self.power_button.bind('<Button-3>',self.power_menu)
        toolbar=frame(self.root,fill='x',padx=self.s(24),pady=(0,self.s(12)))
        self.search_text=tk.StringVar()
        search_label=tk.Label(toolbar,text='Search apps',font=('Segoe UI',10),bg='#00242d',fg='#93a1a1')
        search_label.pack(side='left',padx=(0,self.s(12)))
        self.app_labels.append(search_label)
        self.search_entry=tk.Entry(toolbar,textvariable=self.search_text,font=('Segoe UI',11),relief='flat',bd=0,highlightthickness=1)
        self.search_entry.pack(side='left',fill='x',expand=True,ipady=self.s(8))
        self.search_entry.bind('<Return>',lambda event:self.launch(self.app_visible_items[0]) if self.app_visible_items else None)
        self.app_actions=[]
        for text,action in (('Settings',self.preferences),('+ Add',lambda:self.edit_pin())):
            button=tk.Button(toolbar,text=text,command=action,font=('Segoe UI',10),relief='flat',bd=0,padx=self.s(13),pady=self.s(8),cursor='hand2')
            button.pack(side='right',padx=(self.s(8),0))
            self.app_actions.append(button)
        categories=frame(self.root,fill='x',padx=self.s(24),pady=(0,self.s(14)))
        self.category_buttons={}
        for name in ('All',)+GROUPS:
            button=tk.Button(categories,text=name,command=lambda group=name:self.select_group(group),font=('Segoe UI',10,'bold'),relief='flat',bd=0,padx=self.s(14),pady=self.s(7),cursor='hand2')
            button.pack(side='left',padx=(0,self.s(5)))
            self.category_buttons[name]=button
        footer=frame(self.root,side='bottom',fill='x',padx=self.s(24),pady=self.s(12))
        self.app_status=tk.Label(footer,text='',anchor='w',font=('Segoe UI',9))
        self.app_status.pack(side='left',fill='x',expand=True)
        self.app_labels.append(self.app_status)
        self.clock=tk.Label(footer,text=dt.datetime.now().strftime('%H:%M'),font=('Segoe UI',9))
        self.clock.pack(side='right')
        self.app_labels.append(self.clock)
        body=frame(self.root,fill='both',expand=True,padx=self.s(16))
        self.canvas=tk.Canvas(body,bg='#00242d',highlightthickness=0,yscrollincrement=self.s(24))
        scrollbar=ttk.Scrollbar(body,orient='vertical',command=self.canvas.yview)
        scrollbar.pack(side='right',fill='y')
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.canvas.pack(side='left',fill='both',expand=True)
        self.canvas.bind('<MouseWheel>',lambda event:self.canvas.yview_scroll(-int(event.delta/120),'units'))
        self.search_entry.bind('<Return>',lambda event:self.launch(self.app_visible_items[0]) if self.app_visible_items else None)
        self.search_text.trace_add('write',lambda *args:self.schedule_app_render())
        self.root.update_idletasks()

    def schedule_app_render(self):
        if self.app_render_job is not None:self.root.after_cancel(self.app_render_job)
        self.app_render_job=self.root.after(100,self.render)

    def app_resized(self,event):
        if event.widget!=self.root:return
        size=(event.width,event.height)
        if size!=getattr(self,'last_app_size',None):
            self.last_app_size=size
            self.schedule_app_render()

    def render_app(self):
        if self.app_render_job is not None:self.root.after_cancel(self.app_render_job)
        self.app_render_job=None
        light=self.config.get('LightTheme',False)
        self.bg='#fdf6e3' if light else '#00242d'
        self.border='#93a1a1' if light else '#486b72'
        self.ink='#073642' if light else '#eee8d5'
        self.muted='#586e75' if light else '#93a1a1'
        self.accent='#197e77' if light else '#2aa198'
        self.hover='#e5dfcb' if light else '#0b3943'
        self.card='#eee8d5' if light else '#052d37'
        self.root.configure(bg=self.bg)
        self.configure_atria_widgets()
        self.atria_signature.configure(bg=self.bg)
        self.atria_signature.delete('all')
        self.atria_signature.create_text(self.s(0),self.s(15),text='part of',anchor='w',fill=self.muted,font=('Segoe UI',7))
        def point(x,y):return self.s(49+x*.62),self.s(1+y*.62)
        for coordinates in ((16,4,27,16,16,28,5,16,16,4),(5,40,16,28,27,40)):
            points=[value for x,y in zip(coordinates[::2],coordinates[1::2]) for value in point(x,y)]
            self.atria_signature.create_line(*points,fill=self.accent,width=max(2,self.s(2)),capstyle='round',joinstyle='round')
        self.atria_signature.create_text(self.s(80),self.s(15),text='a t r i a',anchor='w',fill=self.ink,font=(self.atria_font,13))
        for widget in self.app_frames:widget.configure(bg=self.bg)
        for widget in self.app_labels:widget.configure(bg=self.bg,fg=self.ink if widget in self.app_wordmark_labels else self.muted)
        for widget in [self.power_button]+self.app_actions:
            widget.configure(bg=self.hover,fg=self.ink,activebackground=self.border,activeforeground=self.ink)
        self.search_entry.configure(bg=self.card,fg=self.ink,insertbackground=self.ink,highlightbackground=self.border,highlightcolor=self.accent)
        query=self.search_text.get().strip().casefold()
        for name,button in self.category_buttons.items():
            selected=name==('All' if query else self.app_group)
            button.configure(bg=self.hover if selected else self.bg,fg=self.accent if selected else self.muted,activebackground=self.hover,activeforeground=self.ink)
        files={'Id':'files','Name':'File Explorer','Target':str(Path.home()),'ProcessName':'explorer'}
        items=[files]+[item for item in self.config['Items'] if query or self.app_group=='All' or item['Group']==self.app_group]
        if query:items=[item for item in items if query in (item['Name']+' '+item.get('Group','Tools')).casefold()]
        previous=[item['Id'] for item in getattr(self,'app_visible_items',[])]
        self.app_visible_items=items
        self.canvas.delete('all')
        self.canvas.configure(bg=self.bg)
        self.images,self.buttons=[],{}
        width=max(self.canvas.winfo_width(),self.s(500))
        columns=max(3,min(6,width//self.s(140)))
        gap=self.s(10)
        card_width=(width-gap*(columns+1))/columns
        card_height=self.s(max(105,self.config['IconSize']+73))
        for index,item in enumerate(items):
            x=gap+(index%columns)*(card_width+gap)
            y=gap+(index//columns)*(card_height+gap)
            self.app_card(item,x,y,card_width,card_height)
        rows=(len(items)+columns-1)//columns
        self.canvas.configure(scrollregion=(0,0,width,max(self.canvas.winfo_height(),gap+rows*(card_height+gap))))
        if previous!=[item['Id'] for item in items]:self.canvas.yview_moveto(0)
        if not items:self.canvas.create_text(width/2,self.s(70),text='No apps found. Try a different name.',fill=self.muted,font=('Segoe UI',12))
        self.width,self.height=self.root.winfo_width(),self.root.winfo_height()
        self.x,self.y=self.root.winfo_x(),self.root.winfo_y()
        self.expanded_y=self.y
        self.last_area=work_area()
        self.hint_text()
        self.refresh_indicators()
        self.refresh_power_status()
        self.write_runtime()

    def app_card(self,item,x,y,width,height):
        tag='pin-'+item['Id']
        background=self.round_rect(x,y,x+width,y+height,self.s(12),fill=self.card,outline='',tags=tag)
        display_size=min((32,40,48,50,56,60,64,70,72,80,84,90,96,112),key=lambda size:abs(size-self.s(self.config['IconSize'])))
        path=APP_DIR/'icons'/f"{item['Id']}-{display_size}.png"
        if not path.exists():path=APP_DIR/'icons'/f"{item['Id']}-{self.config['IconSize']}.png"
        icon_y=y+self.s(17)+display_size/2
        if path.exists():
            image=tk.PhotoImage(file=str(path))
            self.images.append(image)
            self.canvas.create_image(x+width/2,icon_y,image=image,tags=tag)
        else:self.canvas.create_text(x+width/2,icon_y,text=item['Name'][:2].upper(),fill=self.accent,font=('Segoe UI',18,'bold'),tags=tag)
        self.canvas.create_text(x+width/2,y+height-self.s(31),text=item['Name'],width=width-self.s(12),fill=self.ink,font=('Segoe UI',10),justify='center',tags=tag)
        dot=self.canvas.create_oval(x+width-self.s(14),y+self.s(9),x+width-self.s(8),y+self.s(15),fill=self.accent,outline='',state='hidden')
        self.buttons[item['Id']]=(item,dot)
        def enter(event):
            self.canvas.itemconfigure(background,fill=self.hover)
            self.canvas.configure(cursor='hand2')
            self.hint_text(item['Name'])
        def leave(event):
            self.canvas.itemconfigure(background,fill=self.card)
            self.canvas.configure(cursor='')
            self.hint_text()
        self.canvas.tag_bind(tag,'<Enter>',enter)
        self.canvas.tag_bind(tag,'<Leave>',leave)
        self.canvas.tag_bind(tag,'<Button-1>',lambda event:self.launch(item))
        self.canvas.tag_bind(tag,'<Button-3>',lambda event:self.pin_menu(event,item))

    def focus_search(self):
        self.search_entry.focus_set()
        self.search_entry.selection_range(0,'end')

    def schedule_app_render(self):
        if self.app_render_job is not None:self.root.after_cancel(self.app_render_job)
        self.app_render_job=self.root.after(100,self.render_app)

    def app_resized(self,event):
        if event.widget==self.root:self.schedule_app_render()

    def show_application(self):
        hwnd=self.wrapper_handle()
        if user32.IsIconic(hwnd):user32.ShowWindow(hwnd,9)
        self.root.deiconify()
        bounds=W.RECT()
        if user32.GetWindowRect(hwnd,ctypes.byref(bounds)):
            left,top=user32.GetSystemMetrics(76),user32.GetSystemMetrics(77)
            right,bottom=left+user32.GetSystemMetrics(78),top+user32.GetSystemMetrics(79)
            if bounds.right<=left or bounds.left>=right or bounds.bottom<=top or bounds.top>=bottom:
                area=work_area()
                self.root.geometry(f'+{area[0]+self.s(30)}+{area[1]+self.s(30)}')
        self.root.lift()
        user32.SetForegroundWindow(hwnd)
        if not self.modal:self.focus_search()
        self.root.update_idletasks()
        self.write_runtime()

    def app_preferences(self):
        child,close=self.dialog('Desktop Launcher preferences',410,300)
        ttk.Label(child,text='Desktop Launcher',font=('Segoe UI',18,'bold')).place(x=24,y=20)
        startup=tk.BooleanVar(value=startup_enabled())
        light=tk.BooleanVar(value=self.config.get('LightTheme',False))
        ttk.Checkbutton(child,text='Start with Windows',variable=startup).place(x=26,y=80)
        ttk.Checkbutton(child,text='Light appearance',variable=light).place(x=26,y=120)
        ttk.Label(child,text='Icon size').place(x=26,y=170)
        size=ttk.Combobox(child,state='readonly',values=('32','40','48','56','64','72'),width=8)
        size.set(str(self.config['IconSize']))
        size.place(x=135,y=166)
        def save():
            set_startup(startup.get())
            self.config.update(LightTheme=light.get(),IconSize=int(size.get()),WindowMode='App')
            self.commit()
            close()
        ttk.Button(child,text='Save',command=save).place(x=288,y=245,width=92)
        ttk.Button(child,text='Cancel',command=close).place(x=184,y=245,width=92)

    def error(self, exc_type, exc, trace):
        logging.error('Dock error', exc_info=(exc_type, exc, trace))
        messagebox.showerror('Desktop Launcher', str(exc), parent=self.root)

    def round_rect(self, x1, y1, x2, y2, radius=12, **kwargs):
        points = [x1+radius,y1,x2-radius,y1,x2,y1,x2,y1+radius,x2,y2-radius,x2,y2,x2-radius,y2,x1+radius,y2,x1,y2,x1,y2-radius,x1,y1+radius,x1,y1]
        return self.canvas.create_polygon(points, smooth=True, splinesteps=24, **kwargs)

    def render(self):
        if self.app:return self.render_app()
        light = self.config.get('LightTheme', False)
        self.bg = '#fdf6e3' if light else '#00242d'
        self.border = '#93a1a1' if light else '#486b72'
        self.ink = '#073642' if light else '#eee8d5'
        self.muted = '#586e75' if light else '#93a1a1'
        self.accent = '#197e77' if light else '#2aa198'
        self.hover = '#e5dfcb' if light else '#0b3943'
        self.canvas.delete('all')
        self.images, self.buttons = [], {}
        group = self.config['SelectedGroup']
        items = [item for item in self.config['Items'] if item['Group'] == group]
        size, cell = self.config['IconSize'], self.config['IconSize'] + 18
        longest = max([len([pin for pin in self.config['Items'] if pin['Group'] == g]) for g in GROUPS] + [1])
        left, top, right, bottom = work_area()
        self.last_area=(left,top,right,bottom)
        self.width = min(self.s(max(742, longest * cell + 178)), right-left-self.s(24))
        self.height = self.s(size + 60)
        self.canvas.configure(width=self.width, height=self.height)
        self.round_rect(1, 1, self.width-1, self.height-1, self.s(24), fill=self.bg, outline=self.border, width=1)
        self.canvas.create_line(self.width/2-self.s(20), 2, self.width/2+self.s(20), 2, fill=self.accent, width=2)
        for x, y in ((21,15),(29,15),(21,23),(29,23)):
            self.round_rect(self.s(x),self.s(y),self.s(x+5),self.s(y+5),self.s(1),fill=self.accent,outline='')
        self.canvas.create_text(self.s(43), self.s(22), text='DESKTOP', anchor='w', fill=self.ink, font=('Segoe UI',9,'bold'))
        for index, name in enumerate(GROUPS):
            tag = 'group-'+name
            x = self.s(128+index*70)
            if name == group:
                self.round_rect(x,self.s(8),x+self.s(65),self.s(34),self.s(9),fill=self.hover,outline='',tags=tag)
            self.canvas.create_text(x+self.s(32),self.s(21),text=name,fill=self.accent if name==group else self.muted,font=('Segoe UI',9,'bold'),tags=tag)
            self.canvas.tag_bind(tag,'<Button-1>',lambda event,g=name:self.select_group(g))
            self.canvas.tag_bind(tag,'<Enter>',lambda event:self.canvas.configure(cursor='hand2'))
            self.canvas.tag_bind(tag,'<Leave>',lambda event:self.canvas.configure(cursor=''))
        self.round_rect(self.width-self.s(250),self.s(8),self.width-self.s(86),self.s(34),self.s(9),fill=self.hover,outline='',tags='power-mode')
        self.power_label=self.canvas.create_text(self.width-self.s(168),self.s(21),text='Power mode',fill=self.accent,font=('Segoe UI',9,'bold'),tags='power-mode')
        self.canvas.tag_bind('power-mode','<Button-1>',lambda event:self.toggle_power_mode())
        self.canvas.tag_bind('power-mode','<Button-3>',self.power_menu)
        self.canvas.tag_bind('power-mode','<Enter>',lambda event:self.canvas.configure(cursor='hand2'))
        self.canvas.tag_bind('power-mode','<Leave>',lambda event:self.canvas.configure(cursor=''))
        self.hint = self.canvas.create_text(self.width-self.s(80),self.s(22),text='',state='hidden',anchor='e',fill=self.muted,font=('Segoe UI',8))
        self.refresh_power_status()
        self.clock = self.canvas.create_text(self.width-self.s(19),self.s(22),text=dt.datetime.now().strftime('%H:%M'),anchor='e',fill=self.ink,font=('Segoe UI',9))
        files = {'Id':'files','Name':'File Explorer','Target':str(Path.home()),'IconPath':'','ProcessName':'explorer'}
        self.pin(files,self.s(14),self.s(42),size,cell)
        x = self.s(14+cell+10)
        self.canvas.create_line(self.s(cell+19),self.s(49),self.s(cell+19),self.height-self.s(20),fill=self.border)
        # Reduce tile spacing when the screen is narrower than the configured collection.
        available = self.width-self.s(14+cell+10+90)
        step = min(self.s(cell), available/max(1,len(items)))
        for item in items:
            self.pin(item,x,self.s(42),size,cell,step)
            x += step
        for text, name, x, action in (('+','Add a shortcut',self.width-self.s(77),lambda:self.edit_pin()),('\u2699','Preferences',self.width-self.s(36),self.preferences)):
            tag = 'utility-'+name
            self.canvas.create_text(x,self.s(42+size/2+4),text=text,fill=self.muted,font=('Segoe UI Symbol',22),tags=tag)
            self.canvas.tag_bind(tag,'<Button-1>',lambda event,fn=action:fn())
            self.canvas.tag_bind(tag,'<Enter>',lambda event,n=name:self.hint_text(n))
            self.canvas.tag_bind(tag,'<Leave>',lambda event:self.hint_text())
        self.x = left+(right-left-self.width)//2
        if self.config.get('Position')=='Top':
            self.expanded_y,self.hidden_y=top+self.s(12),top-self.height+self.s(7)
        else:
            self.expanded_y,self.hidden_y=bottom-self.height-self.s(12),bottom-self.s(7)
        self.y = self.expanded_y
        self.hidden = False
        self.root.geometry(f'{self.width}x{self.height}+{self.x}+{self.y}')
        self.refresh_indicators()
        self.write_runtime()

    def write_runtime(self):
        status = {'ProcessId':os.getpid(),'Preview':self.preview,'Group':self.config['SelectedGroup'],
                  'Shortcuts':len(self.config['Items']),'VisiblePins':len(self.buttons),'Geometry':[self.width,self.height,self.x,self.y],
                  'DpiScale':self.scale,'StartWithWindows':startup_enabled(),'Updated':dt.datetime.now().isoformat(),
                  'Position':self.config.get('Position','Bottom'),'DesktopMode':self.config.get('DesktopMode',False),
                  'AutoHide':self.config.get('AutoHide',False),'TemporaryPopup':self.peek_active,
                  'EdgeReveal':self.config.get('EdgeReveal',True),'RevealReason':self.peek_reason,
                  'PowerMode':self.power_state,
                  'ApplicationMode':self.app,'WindowHandle':int(self.wrapper_handle() or 0),'WindowState':self.root.state(),
                  'TaskbarAppId':windows_app.APP_ID if self.app else None,
                  'ApplicationName':windows_app.APP_NAME,'IconSize':self.config['IconSize'],
                  'DesktopParentVerified':bool(getattr(self,'desktop_host',None) and user32.GetParent(self.wrapper_handle())==self.desktop_host)}
        if self.app:status.update(Position='Window',DesktopMode=False,AutoHide=False,EdgeReveal=False,Group=self.app_group)
        (APP_DIR/'runtime.json').write_text(json.dumps(status,indent=2),encoding='utf-8')

    def wrapper_handle(self):
        return user32.GetParent(self.root.winfo_id())

    def attach_to_desktop(self):
        """Parent only our own dock to Explorer's desktop surface."""
        hosts=[]
        callback_type=ctypes.WINFUNCTYPE(W.BOOL,W.HWND,W.LPARAM)
        def visit(hwnd,_):
            if user32.FindWindowExW(hwnd,None,'SHELLDLL_DefView',None):
                hosts.append(hwnd)
            return True
        callback=callback_type(visit)
        user32.EnumWindows(callback,0)
        if not hosts:
            self.embed_attempts=getattr(self,'embed_attempts',0)+1
            if self.embed_attempts<10:
                self.root.after(1000,self.attach_to_desktop)
                return
            logging.error('Explorer desktop surface was not found; using a normal non-topmost dock.')
            self.write_runtime()
            return
        handle=self.wrapper_handle()
        self.desktop_host=hosts[0]
        style=user32.GetWindowLongW(handle,-16)
        user32.SetWindowLongW(handle,-16,(style & ~0x80000000)|0x40000000)
        user32.SetParent(handle,self.desktop_host)
        self.position_window()
        self.write_runtime()

    def position_window(self):
        host=getattr(self,'desktop_host',None)
        if host:
            bounds=W.RECT()
            user32.GetWindowRect(host,ctypes.byref(bounds))
            user32.SetWindowPos(self.wrapper_handle(),None,self.x-bounds.left,self.y-bounds.top,self.width,self.height,0x0014)
        else:
            self.root.geometry(f'+{self.x}+{self.y}')

    def pin(self,item,x,y,size,cell,step=None):
        step = self.s(cell) if step is None else step
        tag = 'pin-'+item['Id']
        bg = self.round_rect(x+2,y-2,x+step-2,y+self.s(size+12),self.s(12),fill=self.hover,outline='',state='hidden',tags=tag)
        display_size=min((32,40,48,50,56,60,64,70,72,80,84,96,112),key=lambda value:abs(value-self.s(size)))
        icon_path = APP_DIR/'icons'/f"{item['Id']}-{display_size}.png"
        if not icon_path.exists():icon_path=APP_DIR/'icons'/f"{item['Id']}-{size}.png"
        cx,cy = x+step/2,y+self.s(size/2+1)
        if icon_path.exists():
            image = tk.PhotoImage(file=str(icon_path))
            self.images.append(image)
            art = self.canvas.create_image(cx,cy,image=image,tags=tag)
        else:
            letters = ''.join(word[0] for word in item['Name'].split()[:2]).upper()
            self.round_rect(cx-self.s(size/2-2),cy-self.s(size/2-2),cx+self.s(size/2-2),cy+self.s(size/2-2),self.s(10),fill='#28687c',outline='',tags=tag)
            art = self.canvas.create_text(cx,cy,text=letters,fill='white',font=('Segoe UI',13,'bold'),tags=tag)
        dot = self.canvas.create_oval(cx-2,y+self.s(size+7),cx+2,y+self.s(size+11),fill=self.accent,outline='',state='hidden')
        self.buttons[item['Id']] = (item,dot)
        def enter(event):
            self.canvas.itemconfigure(bg,state='normal')
            self.canvas.move(art,0,-self.s(3))
            self.canvas.configure(cursor='hand2')
            self.hint_text(item['Name'])
        def leave(event):
            self.canvas.itemconfigure(bg,state='hidden')
            self.canvas.move(art,0,self.s(3))
            self.canvas.configure(cursor='')
            self.hint_text()
        self.canvas.tag_bind(tag,'<Enter>',enter)
        self.canvas.tag_bind(tag,'<Leave>',leave)
        self.canvas.tag_bind(tag,'<Button-1>',lambda event:self.launch(item))
        self.canvas.tag_bind(tag,'<Button-3>',lambda event:self.pin_menu(event,item))

    def hint_text(self,text=''):
        if self.app:
            self.app_status.configure(text=text or 'Click an app to open it. Right-click for options.')
            return
        self.canvas.itemconfigure(self.hint,text=text if len(text)<=27 else text[:25]+'…',state='normal' if text else 'hidden')
        self.canvas.itemconfigure('power-mode',state='hidden' if text else 'normal')

    def refresh_power_status(self):
        previous=self.power_state
        try:
            self.power_state=read_power_mode()
        except (OSError,AttributeError):
            self.power_state={'Mode':'unavailable','Label':'Power settings','Available':False}
        color='#b58900' if self.power_state['Mode']=='performance' else self.accent
        if self.app:self.power_button.configure(text=self.power_state['Label']+' \u21c4',fg=color)
        else:self.canvas.itemconfigure(self.power_label,text=self.power_state['Label']+' \u21c4',fill=color)
        return self.power_state != previous

    def change_power_mode(self,mode):
        try:
            set_power_mode(mode)
        except Exception as exc:
            logging.exception('Changing Windows power mode')
            messagebox.showerror('Windows power mode',f"Couldn't change the power mode.\n\n{exc}\n\nYou can also change it in Settings > System > Power & battery.",parent=self.root)
        finally:
            self.refresh_power_status()
            self.write_runtime()

    def toggle_power_mode(self):
        self.refresh_power_status()
        if not self.power_state.get('Available'):
            os.startfile('ms-settings:powersleep')
            return
        self.change_power_mode('balanced' if self.power_state['Mode']=='performance' else 'performance')

    def build_power_menu(self,parent=None):
        self.refresh_power_status()
        menu=self.menu(parent)
        menu.add_command(label='Windows power mode - '+self.power_state.get('PowerSource','Unavailable'),state='disabled')
        menu.add_separator()
        menu.power_choice=tk.StringVar(value=self.power_state['Mode'])
        for mode,label in (('balanced','Balanced - everyday use'),('performance','Best performance - gaming')):
            menu.add_radiobutton(label=label,variable=menu.power_choice,value=mode,command=lambda choice=mode:self.change_power_mode(choice),state='normal' if self.power_state.get('Available') else 'disabled')
        menu.add_separator()
        menu.add_command(label='Best performance can use more power.',state='disabled')
        menu.add_command(label='Windows power settings',command=lambda:os.startfile('ms-settings:powersleep'))
        return menu

    def power_menu(self,event):
        self.show_menu(self.build_power_menu(),event)

    def select_group(self,group):
        if self.app:
            self.app_group=group
            self.search_text.set('')
        if group in GROUPS:
            self.config['SelectedGroup']=group
            save_config(self.config)
        self.render()

    def update_running(self):
        try:
            self.windows=app_windows()
            self.refresh_indicators()
            if self.app:self.clock.configure(text=dt.datetime.now().strftime('%H:%M'))
            else:self.canvas.itemconfigure(self.clock,text=dt.datetime.now().strftime('%H:%M'))
            if self.refresh_power_status():self.write_runtime()
        except Exception:
            logging.exception('Window status')
        self.root.after(5000,self.update_running)

    def refresh_indicators(self):
        for item,dot in self.buttons.values():
            name=(item.get('ProcessName') or Path(item['Target']).stem).lower()
            self.canvas.itemconfigure(dot,state='normal' if name in self.windows else 'hidden')

    def launch(self,item,new=False):
        try:
            name=(item.get('ProcessName') or Path(item['Target']).stem).lower()
            windows=self.windows.get(name,[]) if item['Id']!='files' else []
            if windows and not new:
                handle=windows[0]
                if user32.IsIconic(handle):
                    user32.ShowWindow(handle,9)
                user32.SetForegroundWindow(handle)
            else:
                open_target(item)
        except Exception as exc:
            logging.exception('Launch %s',item['Name'])
            messagebox.showerror('Desktop Launcher',f"Couldn't open {item['Name']}.\n\n{exc}\n\nRight-click its icon to edit the shortcut.",parent=self.root)

    def menu(self,parent=None):
        return tk.Menu(parent or self.root,tearoff=False,bg=self.bg,fg=self.ink,activebackground=self.hover,activeforeground=self.accent,font=('Segoe UI',10))

    def show_menu(self,menu,event):
        self.modal=True
        def unmap(_):
            self.modal=False
            self.last_inside=dt.datetime.now()
            self.root.after_idle(menu.destroy)
        menu.bind('<Unmap>',unmap)
        try:
            menu.tk_popup(event.x_root,event.y_root)
        finally:
            menu.grab_release()

    def background_menu(self,event):
        if self.canvas.find_withtag('current'):
            tags=self.canvas.gettags('current')
            if any(tag.startswith('pin-') or tag=='power-mode' for tag in tags):
                return
        menu=self.menu()
        menu.add_command(label='Desktop Launcher',state='disabled')
        menu.add_separator()
        menu.add_command(label='Add a shortcut…',command=self.edit_pin)
        menu.add_command(label='Add a folder…',command=self.add_folder)
        menu.add_command(label='Preferences…',command=self.preferences)
        menu.add_cascade(label='Windows power mode',menu=self.build_power_menu(menu))
        if not self.app:menu.add_command(label=('✓ ' if self.config.get('AutoHide') else '')+'Auto-hide',command=self.toggle_hide)
        menu.add_command(label='Open configuration folder',command=lambda:os.startfile(APP_DIR))
        menu.add_separator()
        menu.add_command(label='Quit dock',command=self.root.destroy)
        self.show_menu(menu,event)

    def pin_menu(self,event,item):
        menu=self.menu()
        menu.add_command(label='Open '+item['Name'],command=lambda:self.launch(item))
        menu.add_command(label='Open a new instance',command=lambda:self.launch(item,True))
        if item['Id']!='files':
            menu.add_separator()
            menu.add_command(label='Edit shortcut…',command=lambda:self.edit_pin(item))
            menu.add_command(label='Choose icon…',command=lambda:self.choose_icon(item))
            menu.add_command(label='Move left',command=lambda:self.move_pin(item,-1))
            menu.add_command(label='Move right',command=lambda:self.move_pin(item,1))
            menu.add_command(label='Remove from dock',command=lambda:self.remove_pin(item))
        menu.add_separator()
        menu.add_command(label='Preferences…',command=self.preferences)
        self.show_menu(menu,event)

    def rebuild_icons(self):
        helper=Path(__file__).with_name('Prepare-Icons.ps1')
        subprocess.run(['powershell.exe','-NoProfile','-File',str(helper)],creationflags=0x08000000,check=True)

    def commit(self,icons=False):
        save_config(self.config)
        if icons:
            self.rebuild_icons()
        self.render()

    def move_pin(self,item,delta):
        group=[p for p in self.config['Items'] if p['Group']==item['Group']]
        index=group.index(item)
        if 0<=index+delta<len(group):
            other=group[index+delta]
            a,b=self.config['Items'].index(item),self.config['Items'].index(other)
            self.config['Items'][a],self.config['Items'][b]=other,item
            self.commit()

    def remove_pin(self,item):
        self.config['Items'].remove(item)
        self.commit()

    def add_folder(self):
        self.modal=True
        try:
            path=filedialog.askdirectory(parent=self.root,title='Add a folder to the dock')
            if path:
                self.config['Items'].append({'Id':uuid.uuid4().hex,'Name':Path(path).name or path,'Target':path,'Group':self.config['SelectedGroup'],'IconPath':str(Path(os.environ['WINDIR'])/'explorer.exe')})
                self.commit(True)
        finally:
            self.modal=False

    def choose_icon(self,item):
        self.modal=True
        try:
            path=filedialog.askopenfilename(parent=self.root,title='Choose an icon',filetypes=[('Icons and images','*.png *.ico *.exe *.dll *.jpg')])
            if path:
                item['IconPath']=path
                for cache in (APP_DIR/'icons').glob(item['Id']+'-*.png'):
                    cache.unlink()
                self.commit(True)
        finally:
            self.modal=False

    def configure_atria_widgets(self):
        style=ttk.Style(self.root)
        if style.theme_use()!='clam':style.theme_use('clam')
        style.configure('.',background=self.bg,foreground=self.ink,fieldbackground=self.card,
                        bordercolor=self.border,lightcolor=self.border,darkcolor=self.border,
                        selectbackground=self.accent,selectforeground=self.bg,font=('Segoe UI',10))
        style.configure('TButton',background=self.hover,padding=6)
        style.map('TButton',background=[('active',self.card)],foreground=[('disabled',self.muted)])
        style.map('TCheckbutton',background=[('active',self.hover)])
        style.map('TCombobox',fieldbackground=[('readonly',self.card)],foreground=[('readonly',self.ink)])
        self.root.option_add('*TCombobox*Listbox.background',self.card)
        self.root.option_add('*TCombobox*Listbox.foreground',self.ink)

    def dialog(self,title,width=470,height=340):
        self.modal=True
        child=tk.Toplevel(self.root)
        child.title(title)
        child.attributes('-topmost',True)
        child.resizable(False,False)
        child.configure(bg=self.bg)
        child.geometry(f'{width}x{height}+{max(0,self.x+(self.width-width)//2)}+{max(0,self.expanded_y-height-18)}')
        if self.app:
            child.geometry(f'{width}x{height}+{max(0,self.root.winfo_rootx()+(self.root.winfo_width()-width)//2)}+{max(0,self.root.winfo_rooty()+(self.root.winfo_height()-height)//2)}')
        child.grab_set()
        def close():
            self.modal=False
            child.destroy()
        child.protocol('WM_DELETE_WINDOW',close)
        child.bind('<Escape>',lambda event:close())
        return child,close

    def edit_pin(self,item=None):
        child,close=self.dialog('Edit shortcut' if item else 'Add a shortcut',510,332)
        fields={}
        for index,(label,key) in enumerate((('Name','Name'),('App, shortcut, folder or URL','Target'),('Arguments (optional)','Arguments'))):
            ttk.Label(child,text=label).place(x=22,y=18+index*66)
            entry=ttk.Entry(child)
            entry.place(x=22,y=41+index*66,width=466 if key!='Target' else 371)
            entry.insert(0,(item or {}).get(key) or '')
            fields[key]=entry
        def browse():
            path=filedialog.askopenfilename(parent=child,title='Choose an app or shortcut',filetypes=[('Apps and shortcuts','*.exe *.lnk *.url'),('All files','*.*')])
            if path:
                fields['Target'].delete(0,'end');fields['Target'].insert(0,path)
                if not fields['Name'].get():fields['Name'].insert(0,Path(path).stem)
        ttk.Button(child,text='Browse',command=browse).place(x=404,y=107,width=84)
        ttk.Label(child,text='Collection').place(x=22,y=221)
        group=ttk.Combobox(child,state='readonly',values=GROUPS)
        group.set((item or {}).get('Group',self.config['SelectedGroup']));group.place(x=22,y=244,width=160)
        def save():
            data={key:entry.get().strip() for key,entry in fields.items()}
            if not data['Name'] or not data['Target']:
                messagebox.showinfo('Desktop Launcher','Enter a name and target.',parent=child);return
            if item is None:
                data.update(Id=uuid.uuid4().hex,Group=group.get(),IconPath='',ProcessName='')
                self.config['Items'].append(data)
            else:
                if data['Target']!=item['Target']:item.update(IconPath='',ProcessName='',WorkingDirectory='')
                item.update(data,Group=group.get())
            self.commit(True);close()
        ttk.Button(child,text='Save shortcut',command=save).place(x=350,y=287,width=138)
        ttk.Button(child,text='Cancel',command=close).place(x=248,y=287,width=92)
        fields['Name'].focus_set()

    def preferences(self):
        if self.app:return self.app_preferences()
        child,close=self.dialog('Desktop Launcher preferences',410,432)
        ttk.Label(child,text='Make it yours',font=('Segoe UI',18,'bold')).place(x=24,y=18)
        startup=tk.BooleanVar(value=startup_enabled())
        hide=tk.BooleanVar(value=self.config.get('AutoHide',False))
        light=tk.BooleanVar(value=self.config.get('LightTheme',False))
        desktop=tk.BooleanVar(value=self.config.get('DesktopMode',False))
        edge=tk.BooleanVar(value=self.config.get('EdgeReveal',True))
        for index,(text,var) in enumerate((('Start with Windows',startup),('Auto-hide when the pointer leaves',hide),('Light appearance',light),('Keep on desktop, behind app windows',desktop))):
            ttk.Checkbutton(child,text=text,variable=var).place(x=26,y=74+index*34)
        ttk.Checkbutton(child,text='Reveal at the screen edge',variable=edge).place(x=26,y=210)
        ttk.Label(child,text='Icon size').place(x=26,y=262)
        size=ttk.Combobox(child,state='readonly',values=('32','40','48','56','64','72'),width=8)
        size.set(str(self.config['IconSize']));size.place(x=135,y=258)
        ttk.Label(child,text='Position').place(x=26,y=304)
        position=ttk.Combobox(child,state='readonly',values=('Top','Bottom'),width=14)
        position.set(self.config.get('Position','Bottom'));position.place(x=135,y=300)
        ttk.Label(child,text='Ctrl+Alt+D reveals the dock').place(x=26,y=348)
        def save():
            set_startup(startup.get())
            mode_changed=desktop.get()!=self.config.get('DesktopMode',False)
            self.config.update(AutoHide=hide.get(),LightTheme=light.get(),IconSize=int(size.get()),DesktopMode=desktop.get(),Position=position.get(),EdgeReveal=edge.get())
            self.commit();close()
            if mode_changed:
                subprocess.Popen([PYTHONW,SCRIPT,'--restart'])
                self.root.destroy()
        ttk.Button(child,text='Save',command=save).place(x=288,y=386,width=92)
        ttk.Button(child,text='Cancel',command=close).place(x=184,y=386,width=92)

    def toggle_hide(self):
        if self.app:return
        self.config['AutoHide']=not self.config.get('AutoHide',False)
        self.commit()

    def reveal(self):
        self.y=self.expanded_y
        self.hidden=False
        self.last_inside=dt.datetime.now()
        self.position_window()
        if not self.config.get('DesktopMode',False):self.root.lift()

    def show_popup(self,reason='command'):
        if self.app:
            self.show_application()
            return
        if self.config.get('DesktopMode') and not self.preview and not self.peek_active:
            handle=self.wrapper_handle()
            user32.SetParent(handle,None)
            style=(user32.GetWindowLongW(handle,-16) & ~0x40000000)|0x80000000
            user32.SetWindowLongW(handle,-16,W.LONG(style).value)
            self.desktop_host=None
        self.peek_active=True
        self.peek_reason=reason
        self.peek_deadline=time.monotonic()+(.8 if reason=='edge' else 15)
        self.root.attributes('-topmost',True)
        self.reveal()
        if reason=='edge':
            user32.ShowWindow(self.wrapper_handle(),4)
            user32.SetWindowPos(self.wrapper_handle(),W.HWND(-1),self.x,self.y,self.width,self.height,0x0010)
        else:
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
        if self.peek_job is not None:self.root.after_cancel(self.peek_job)
        self.peek_job=self.root.after(150 if reason=='edge' else 1000,self.finish_peek)
        self.write_runtime()

    def finish_peek(self,force=False):
        if self.peek_job is not None:
            self.root.after_cancel(self.peek_job)
            self.peek_job=None
        if not self.peek_active:return
        x,y=self.root.winfo_pointerxy()
        inside=self.x<=x<=self.x+self.width and self.y<=y<=self.y+self.height
        edge_hover=self.peek_reason=='edge' and self.at_reveal_edge(x,y)
        if self.peek_reason=='edge' and (inside or edge_hover):self.peek_deadline=time.monotonic()+.8
        if not force and (self.modal or inside or edge_hover or time.monotonic()<self.peek_deadline):
            self.peek_job=self.root.after(150 if self.peek_reason=='edge' else 1000,self.finish_peek)
            return
        self.peek_active=False
        self.edge_cooldown=time.monotonic()+.7
        self.root.attributes('-topmost',self.preview or not self.config.get('DesktopMode',False))
        if self.config.get('DesktopMode') and not self.preview:self.attach_to_desktop()
        self.write_runtime()

    def poll_hide(self):
        if self.config.get('AutoHide') and not self.preview and not self.peek_active:
            x,y=self.root.winfo_pointerxy()
            inside=self.x<=x<=self.x+self.width and self.y<=y<=self.y+self.height
            if inside or self.modal:
                self.last_inside=dt.datetime.now()
            should_hide=not inside and not self.modal and (dt.datetime.now()-self.last_inside).total_seconds()>1.2
            destination=self.hidden_y if should_hide else self.expanded_y
            distance=destination-self.y
            if distance:
                self.y += (1 if distance>0 else -1)*max(1,abs(distance)//3)
                self.position_window()
                self.root.after(16,self.poll_hide);return
            self.hidden=should_hide
        self.root.after(250 if self.config.get('AutoHide') else 1000,self.poll_hide)

    def at_reveal_edge(self,x,y):
        if not self.x<=x<=self.x+self.width:return False
        top,bottom=self.last_area[1],self.last_area[3]
        return top<=y<=top+self.s(3) if self.config.get('Position')=='Top' else bottom-self.s(3)<=y<=bottom

    def edge_update(self,x,y,now=None):
        now=time.monotonic() if now is None else now
        if not self.at_reveal_edge(x,y):
            self.edge_entered=None
            self.edge_armed=True
            return
        if self.edge_entered is None:self.edge_entered=now
        if self.edge_armed and not self.peek_active and not self.modal and now>self.edge_cooldown and now-self.edge_entered>=.3:
            self.edge_armed=False
            self.show_popup(reason='edge')

    def poll_edge(self):
        if self.config.get('EdgeReveal',True) and not self.preview:
            try:self.edge_update(*self.root.winfo_pointerxy())
            except tk.TclError:pass
        self.root.after(100 if self.config.get('EdgeReveal',True) else 1000,self.poll_edge)

    def poll_display(self):
        request=APP_DIR/'control-request.json'
        if request.exists():
            try:
                action=json.loads(request.read_text(encoding='utf-8'))['Action']
                request.unlink()
                self.show_popup()
                if action=='settings':self.preferences()
            except (OSError,ValueError,KeyError):
                logging.exception('Dock control request')
        if work_area()!=self.last_area:
            self.render()
        self.root.after(2000,self.poll_display)

    def hotkey_loop(self):
        self.hotkey_thread_id=kernel32.GetCurrentThreadId()
        self.hotkey=bool(user32.RegisterHotKey(None,321,0x4003,ord('D')))
        self.hotkey_ready.set()
        if not self.hotkey:
            return
        message=W.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(message),None,0,0)>0:
                if message.message==0x312:
                    self.root.after(0,self.hotkey_trigger)
        finally:
            user32.UnregisterHotKey(None,321)

    def hotkey_trigger(self):
        self.show_popup()

    def shutdown_hotkey(self):
        if self.hotkey_thread_id:
            user32.PostThreadMessageW(self.hotkey_thread_id,0x12,0,0)

    def run(self):
        try:
            self.root.mainloop()
        finally:
            self.shutdown_hotkey()


def self_test(config):
    missing=[]
    for item in config['Items']:
        if not item.get('Name') or not item.get('Target'):
            missing.append(item.get('Id'))
        elif not item['Target'].lower().startswith(('shell:','steam:','http:','https:')) and not Path(item['Target']).exists():
            missing.append(item['Name'])
    cache=[p for p in config['Items'] if (APP_DIR/'icons'/f"{p['Id']}-{config['IconSize']}.png").exists()]
    return {'Passed':not missing and len(cache)==len(config['Items']),'Items':len(config['Items']),'CachedIcons':len(cache),'MissingTargets':missing,'Groups':{group:sum(pin['Group']==group for pin in config['Items']) for group in GROUPS},'StartWithWindows':startup_enabled()}


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--preview',action='store_true')
    parser.add_argument('--app',action='store_true',help='Open the regular launcher application (default).')
    parser.add_argument('--configure-app',action='store_true',help=argparse.SUPPRESS)
    parser.add_argument('--self-test',type=Path)
    parser.add_argument('--smoke-test',type=Path)
    parser.add_argument('--restart',action='store_true')
    parser.add_argument('--show',action='store_true')
    parser.add_argument('--settings',action='store_true')
    parser.add_argument('--visibility-test',type=Path)
    arguments=parser.parse_args()
    try:
        configuration=load_config()
        if arguments.configure_app:
            configuration['WindowMode']='App'
            save_config(configuration)
            sys.exit(0)
        if arguments.self_test:
            result=self_test(configuration)
            arguments.self_test.write_text(json.dumps(result,indent=2),encoding='utf-8')
            sys.exit(0 if result['Passed'] else 1)
        if arguments.visibility_test:
            dock=Dock(configuration,preview=False)
            checks={}
            try:
                dock.root.update()
                checks['InitiallyOnDesktop']=bool(dock.desktop_host and user32.GetParent(dock.wrapper_handle())==dock.desktop_host)
                dock.show_popup()
                dock.root.update()
                checks['PopupDetached']=not bool(user32.GetParent(dock.wrapper_handle()))
                checks['PopupAboveWindows']=bool(user32.GetWindowLongW(dock.wrapper_handle(),-20)&8)
                checks['PopupVisible']=bool(user32.IsWindowVisible(dock.wrapper_handle()))
                dock.finish_peek(force=True)
                dock.root.update()
                checks['ReturnedToDesktop']=bool(dock.desktop_host and user32.GetParent(dock.wrapper_handle())==dock.desktop_host and not dock.peek_active)
                checks['AlwaysOnTopCleared']=not bool(user32.GetWindowLongW(dock.wrapper_handle(),-20)&8)
                dock.hotkey_trigger()
                checks['HotkeyShowsPopup']=dock.peek_active
                dock.finish_peek(force=True)
                center=dock.x+dock.width//2
                top=dock.last_area[1]
                checks['EdgeHitArea']=dock.at_reveal_edge(center,top) and not dock.at_reveal_edge(center,top+100)
                dock.edge_cooldown=0
                dock.edge_update(center,top,100)
                checks['EdgeHoverDelay']=not dock.peek_active
                dock.edge_update(center,top,100.4)
                checks['EdgeShowsPopup']=dock.peek_active and dock.peek_reason=='edge'
                dock.finish_peek(force=True)
                checks['AutoHideDisabled']=not configuration.get('AutoHide')
            finally:
                dock.shutdown_hotkey()
                dock.root.destroy()
            arguments.visibility_test.write_text(json.dumps({'Passed':all(checks.values()),'Checks':checks},indent=2),encoding='utf-8')
            sys.exit(0 if all(checks.values()) else 1)
        if arguments.smoke_test:
            original=CONFIG.read_bytes()
            dock=Dock(configuration,preview=True)
            dock.root.withdraw()
            checks={}
            try:
                dock.hotkey_ready.wait(2)
                checks['HotkeyRegistered']=dock.hotkey
                for group in GROUPS:
                    dock.select_group(group)
                    dock.root.update_idletasks()
                    expected=1+sum(pin['Group']==group for pin in configuration['Items'])
                    checks[group+'Pins']=len(dock.buttons)==expected
                    checks[group+'Icons']=len(dock.images)==expected
                dock.preferences()
                dialog=[child for child in dock.root.winfo_children() if isinstance(child,tk.Toplevel)][0]
                checks['PreferencesOpened']=dialog.title()=='Desktop Launcher preferences'
                [widget for widget in dialog.winfo_children() if isinstance(widget,ttk.Button) and widget.cget('text')=='Cancel'][0].invoke()
                dock.edit_pin()
                dialog=[child for child in dock.root.winfo_children() if isinstance(child,tk.Toplevel)][0]
                checks['ShortcutEditorOpened']=sum(type(child) is ttk.Entry for child in dialog.winfo_children())==3
                [widget for widget in dialog.winfo_children() if isinstance(widget,ttk.Button) and widget.cget('text')=='Cancel'][0].invoke()
                first=configuration['Items'][0]
                dock.move_pin(first,1)
                checks['Reorder']=configuration['Items'][1]['Id']==first['Id']
                dock.move_pin(first,-1)
                checks['ReorderUndo']=configuration['Items'][0]['Id']==first['Id']
                configuration['LightTheme']=True
                configuration['IconSize']=56
                dock.render()
                checks['ThemeAndSize']=dock.bg=='#fdf6e3' and len(dock.images)==len(dock.buttons)
                checks['ConfigRoundTrip']=len(load_config()['Items'])==len(configuration['Items'])
            finally:
                CONFIG.write_bytes(original)
                dock.shutdown_hotkey()
                dock.root.destroy()
            arguments.smoke_test.write_text(json.dumps({'Passed':all(checks.values()),'Checks':checks},indent=2),encoding='utf-8')
            sys.exit(0 if all(checks.values()) else 1)
        mutex_name='Local\\PersonalDesktopDock_'+os.environ['USERNAME']
        mutex=kernel32.CreateMutexW(None,False,mutex_name)
        exists=kernel32.GetLastError()==183
        if exists and arguments.restart:
            import time
            for attempt in range(40):
                kernel32.CloseHandle(mutex)
                time.sleep(.1)
                mutex=kernel32.CreateMutexW(None,False,mutex_name)
                exists=kernel32.GetLastError()==183
                if not exists:break
        if exists:
            try:
                owner=json.loads((APP_DIR/'runtime.json').read_text(encoding='utf-8'))['ProcessId']
                user32.AllowSetForegroundWindow(W.DWORD(owner))
            except (OSError,ValueError,KeyError):pass
            request=APP_DIR/'control-request.json'
            temporary=request.with_suffix('.tmp')
            temporary.write_text(json.dumps({'Action':'settings' if arguments.settings else 'show'}),encoding='utf-8')
            os.replace(temporary,request)
            kernel32.CloseHandle(mutex)
            sys.exit(0)
        try:
            dock=Dock(configuration,arguments.preview,app=not arguments.preview)
            if arguments.settings:dock.root.after(400,lambda:(dock.show_popup(),dock.preferences()))
            elif arguments.show:dock.root.after(400,dock.show_popup)
            elif dock.app:dock.root.after(150,dock.show_application)
            dock.run()
        finally:
            kernel32.CloseHandle(mutex)
    except Exception:
        logging.exception('Startup failure')
        raise
