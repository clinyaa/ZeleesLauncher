"""Аккаунты Microsoft, аватар и Premium."""
import base64
import os
import threading
import time
import tkinter as tk
from tkinter import messagebox

from . import auth, net
from .app_util import log_to_file
from .config import APP_NAME, CACHE_DIR
from .dialogs import MsLoginDialog
from .servers_page import PremiumDialog
from .ui_kit import C, face_photo, head_url, offline_face

class AccountsMixin:
    """Аккаунты Microsoft, аватар и Premium."""

    # ------------------------------------------------------------------ аккаунты
    def _refresh_accounts(self, select_key=None):
        key = select_key or self.settings.get("last_account")
        acc = self.accounts.find(key) if key else None
        if not acc and self.accounts.items:
            acc = self.accounts.items[0]
        self.cur_acc_key = self.accounts.key(acc) if acc else None
        if self.cur_acc_key:
            self.settings.set("last_account", self.cur_acc_key)
        self._draw_account_pill()
        self.sync_premium()

    def set_skin_head(self, uuid, grid):
        """Голова для аватара берётся прямо из текущего скина."""
        self._skin_grids[uuid] = grid
        for k in [k for k in self._heads if isinstance(k, tuple) and uuid in k]:
            self._heads.pop(k, None)
        self._draw_account_pill()

    def _avatar(self, acc, size):
        if acc["id"] in self._skin_grids:
            key = ("skin", acc["id"], size)
            if key not in self._heads:
                self._heads[key] = face_photo(self._skin_grids[acc["id"]], size)
            return self._heads[key]
        img = self._heads.get((acc["id"], size))
        if img:
            return img
        self.bg(self._load_head, acc["id"], size)
        # пока голова скина загружается — пиксельное лицо-заглушка по нику
        key = ("off", acc.get("name"), size)
        if key not in self._heads:
            self._heads[key] = face_photo(offline_face(acc.get("name", "?")), size)
        return self._heads[key]

    def _load_head(self, uuid, size):
        if (uuid, size) in self._heads or ("loading", uuid, size) in self._heads:
            return
        self._heads[("loading", uuid, size)] = True
        path = os.path.join(CACHE_DIR, "heads", "%s_%d.png" % (uuid, size))
        data = None
        try:
            if os.path.isfile(path) and time.time() - os.path.getmtime(path) < 86400 * 3:
                with open(path, "rb") as f:
                    data = f.read()
            else:
                _, data = net.request(head_url(uuid, size), timeout=10)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "wb") as f:
                    f.write(data)
        except Exception:  # noqa: BLE001
            if os.path.isfile(path):
                with open(path, "rb") as f:
                    data = f.read()
        if data:
            self.ui(self._head_loaded, uuid, size, data)

    def _head_loaded(self, uuid, size, data):
        try:
            self._heads[(uuid, size)] = tk.PhotoImage(data=base64.b64encode(data))
        except tk.TclError:
            return
        self._draw_account_pill()

    def _draw_account_pill(self):
        acc = self.accounts.find(self.cur_acc_key) if self.cur_acc_key else None
        if acc:
            img = self._avatar(acc, self.px(22) // 8 * 8 or 16)
            self.acc_pill.config(text="%s   ·   лицензия   ▾" % acc["name"], image=img)
        else:
            self.acc_pill.config(text="Войти через Microsoft   ▾", image=self.A.icon("user", "_w"))
        self._update_premium_badge()

    # ------------------------------------------------------------------ Premium
    def current_ms_account(self):
        """Текущий аккаунт, если он лицензионный (Premium привязан только к ним)."""
        acc = self.accounts.find(self.cur_acc_key) if self.cur_acc_key else None
        return acc if acc and acc.get("type") == "microsoft" else None

    def _update_premium_badge(self):
        acc = self.current_ms_account()
        if acc and self.premium.is_premium(acc["id"]):
            self.prem_badge.place(self.acc_pill.bx - self.px(10), self.acc_pill.by)
        else:
            self.prem_badge.hide()

    def open_premium(self):
        PremiumDialog(self)

    def ms_call(self, fn, done):
        """fn(acc) в фоне со свежим токеном лицензионного аккаунта; затем done(result, error) в окне."""
        acc = self.current_ms_account()
        if not acc:
            done(None, RuntimeError("Нужен лицензионный аккаунт Microsoft"))
            return
        key = self.accounts.key(acc)

        def work():
            res, err = None, None
            try:
                fresh = auth.refresh_account(acc)
                if fresh is not acc:
                    self.accounts.upsert(fresh)
                res = fn(fresh)
            except Exception as e:  # noqa: BLE001
                log_to_file("premium %s: %s" % (key, e))
                err = e
            self.ui(self._ms_call_done, done, res, err)
        threading.Thread(target=work, daemon=True).start()

    def _ms_call_done(self, done, res, err):
        self._update_premium_badge()
        self.servers_page.on_premium_changed()
        done(res, err)

    def sync_premium(self, done=None):
        """Обновляет статус Premium текущего аккаунта с сайта."""
        acc = self.current_ms_account()
        if not acc:
            self._update_premium_badge()
            if done:
                done(None, None)
            return
        if acc["id"] in self._premium_syncing and not done:
            return
        self._premium_syncing.add(acc["id"])

        def finished(res, err, uid=acc["id"]):
            self._premium_syncing.discard(uid)
            if done:
                done(res, err)
        self.ms_call(self.premium.me, finished)

    def _account_menu(self):
        m = tk.Menu(self.root, tearoff=0, bg=C["panel2"], fg=C["fg"], activebackground=C["sel"],
                    activeforeground="white", bd=0, relief="flat", font=self.F.b(10))
        for a in self.accounts.items:
            k = self.accounts.key(a)
            mark = "✓  " if k == self.cur_acc_key else "     "
            m.add_command(label="%s%s" % (mark, a["name"]), command=lambda kk=k: self._select_account(kk))
        if self.accounts.items:
            m.add_separator()
        m.add_command(label="     Войти через Microsoft…", command=self._add_microsoft)
        m.add_separator()
        m.add_command(label="★   Premium…", command=self.open_premium, foreground=C["gold"],
                      activeforeground=C["gold"])
        if self.cur_acc_key:
            acc = self.accounts.find(self.cur_acc_key)
            m.add_separator()
            m.add_command(label="     Удалить «%s»" % acc["name"], command=self._remove_account)
        p = self.acc_pill
        x = self.hc.winfo_rootx() + p.bx
        y = self.hc.winfo_rooty() + p.by + p.h + self.px(6)
        m.tk_popup(x, y)

    def _select_account(self, key):
        self.settings.set("last_account", key)
        self._refresh_accounts(key)

    def _add_microsoft(self):
        MsLoginDialog(self, self.s_client.get().strip() or auth.AZURE_CLIENT_ID)

    def ms_account_added(self, acc):
        self._refresh_accounts(self.accounts.upsert(acc))
        self.status("Вход выполнен: %s — лицензия подтверждена" % acc["name"], C["accent"])

    def _remove_account(self):
        k = self.cur_acc_key
        a = self.accounts.find(k) if k else None
        if a and messagebox.askyesno(APP_NAME, "Удалить аккаунт %s?" % a.get("name"), parent=self.root):
            self.accounts.remove(k)
            threading.Thread(target=self.premium.forget, args=(a["id"],), daemon=True).start()
            self.settings.set("last_account", "")
            self._refresh_accounts()
