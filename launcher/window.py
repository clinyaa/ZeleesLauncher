"""Окно без системной рамки: своя полоса заголовка, перетаскивание, свернуть/закрыть (Windows)."""
import sys
import tkinter as tk

from .config import APP_VERSION
from .ui_kit import C

IS_WIN = sys.platform.startswith("win")
TB_H = 34


def set_app_id():
    """Своя иконка и группировка на панели задач Windows (даже при запуске через python)."""
    if IS_WIN:
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Zelees.ZeleesLauncher")
        except Exception:  # noqa: BLE001
            pass


def _hwnd(root):
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    user32.GetParent.restype = wintypes.HWND
    user32.GetParent.argtypes = [wintypes.HWND]
    return user32.GetParent(root.winfo_id())


def make_borderless(root):
    """Убирает системный заголовок и рамку, оставляя окно на панели задач, Alt+Tab и сворачивание.
    Возвращает True, если получилось (только Windows).

    Рамку снимает сам Tk (overrideredirect), а не правка стиля окна в обход него: иначе Tk продолжает
    считать размер окна с учётом рамки и при любом обновлении возвращает её — окно становилось
    больше содержимого (пустые полосы справа и снизу) и растягивалось при перетаскивании."""
    if not IS_WIN:
        return False
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        GWL_STYLE, GWL_EXSTYLE = -16, -20
        WS_MINIMIZEBOX, WS_SYSMENU = 0x00020000, 0x00080000
        WS_EX_TOOLWINDOW, WS_EX_APPWINDOW = 0x00000080, 0x00040000
        if ctypes.sizeof(ctypes.c_void_p) == 8:
            get, set_ = user32.GetWindowLongPtrW, user32.SetWindowLongPtrW
        else:
            get, set_ = user32.GetWindowLongW, user32.SetWindowLongW
        get.restype = ctypes.c_ssize_t
        get.argtypes = [wintypes.HWND, ctypes.c_int]
        set_.restype = ctypes.c_ssize_t
        set_.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        root.overrideredirect(True)
        root.update_idletasks()
        hwnd = _hwnd(root)
        # окно без рамки Windows не показывает на панели задач — возвращаем его туда,
        # а WS_MINIMIZEBOX | WS_SYSMENU нужны, чтобы оно сворачивалось и разворачивалось кликом по значку
        set_(hwnd, GWL_EXSTYLE, (get(hwnd, GWL_EXSTYLE) & ~WS_EX_TOOLWINDOW) | WS_EX_APPWINDOW)
        set_(hwnd, GWL_STYLE, get(hwnd, GWL_STYLE) | WS_MINIMIZEBOX | WS_SYSMENU)
        # SWP_FRAMECHANGED | SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER
        user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0020 | 0x0002 | 0x0001 | 0x0004)
        # панель задач подхватывает новый стиль только при повторном показе окна
        root.withdraw()
        root.deiconify()
        try:  # Windows 11: скруглённые углы и тонкая тёмная обводка
            dwm = ctypes.windll.dwmapi
            pref = ctypes.c_int(2)          # DWMWCP_ROUND
            dwm.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref), 4)
            border = ctypes.c_int(0x00332E2A)  # COLORREF (BGR) = #2A2E33
            dwm.DwmSetWindowAttribute(hwnd, 34, ctypes.byref(border), 4)
        except Exception:  # noqa: BLE001
            pass
        return True
    except Exception:  # noqa: BLE001
        return False


class TitleBar:
    """Полоса заголовка: логотип, версия, перетаскивание окна, свернуть и закрыть."""

    def __init__(self, app, width):
        self.app = app
        A, px = app.A, app.px
        self.root = app.root
        self.h = px(TB_H)
        self.w = width
        cv = tk.Canvas(app.root, width=width, height=self.h, bg=C["side"], highlightthickness=0, bd=0)
        cv.place(x=0, y=0)
        self.cv = cv
        cv.create_rectangle(0, 0, width, self.h, fill=C["side"], width=0, tags="drag")
        cv.create_line(0, self.h - 1, width, self.h - 1, fill="#16181e")
        x = px(16)
        cv.create_image(x, self.h // 2, image=A.title_mark(), anchor="w", tags="drag")
        x += A.title_mark().width() + px(10)
        wm = A.wordmark(max(1, round(2 * A.s)))
        cv.create_image(x, self.h // 2 + 1, image=wm, anchor="w", tags="drag")
        x += wm.width() + px(10)
        cv.create_text(x, self.h // 2 + 1, text="v" + APP_VERSION, anchor="w", fill=C["dim"],
                       font=app.F.b(8), tags="drag")
        self.btn_w = px(46)
        self._button("close", width - self.btn_w, "#e81123", self._close)
        self._button("min", width - 2 * self.btn_w, "#22252d", self._minimize)
        cv.tag_bind("drag", "<ButtonPress-1>", self._press)
        cv.tag_bind("drag", "<B1-Motion>", self._move)
        cv.tag_bind("drag", "<ButtonRelease-1>", self._release)
        self._off = None

    def _button(self, kind, x, hover, cmd):
        cv, px, h = self.cv, self.app.px, self.h
        tag = "btn_" + kind
        bg = cv.create_rectangle(x, 0, x + self.btn_w, h - 1, fill=C["side"], width=0, tags=(tag,))
        cx, cy, r = x + self.btn_w // 2, h // 2, px(5)
        if kind == "close":
            lines = [cv.create_line(cx - r, cy - r, cx + r + 1, cy + r + 1, fill=C["muted"], width=max(1, px(1.3)),
                                    tags=(tag,)),
                     cv.create_line(cx - r, cy + r, cx + r + 1, cy - r - 1, fill=C["muted"], width=max(1, px(1.3)),
                                    tags=(tag,))]
        else:
            lines = [cv.create_line(cx - r, cy, cx + r + 1, cy, fill=C["muted"], width=max(1, px(1.3)), tags=(tag,))]

        def enter(_e):
            cv.itemconfigure(bg, fill=hover)
            for ln in lines:
                cv.itemconfigure(ln, fill="white")

        def leave(_e):
            cv.itemconfigure(bg, fill=C["side"])
            for ln in lines:
                cv.itemconfigure(ln, fill=C["muted"])
        cv.tag_bind(tag, "<Enter>", enter)
        cv.tag_bind(tag, "<Leave>", leave)
        cv.tag_bind(tag, "<ButtonRelease-1>", lambda e: cmd())

    # ---- перетаскивание: двигаем окно сами, без системного цикла перемещения Windows
    # (WM_NCLBUTTONDOWN / SC_MOVE): вызванный из обработчика Tk, он ронял Python (Fatal Python error)
    def _press(self, e):
        self._off = (e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y())

    def _move(self, e):
        if self._off:
            self.root.geometry("+%d+%d" % (e.x_root - self._off[0], e.y_root - self._off[1]))

    def _release(self, _e):
        self._off = None

    def _minimize(self):
        if IS_WIN and self.app.borderless:
            try:  # Tk не умеет сворачивать окно с overrideredirect — сворачиваем средствами Windows
                import ctypes
                ctypes.windll.user32.ShowWindow(_hwnd(self.root), 6)  # SW_MINIMIZE
                return
            except Exception:  # noqa: BLE001
                pass
        self.root.iconify()

    def _close(self):
        self.app._on_close()
