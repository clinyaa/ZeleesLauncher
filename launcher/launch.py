"""Сборка командной строки и запуск Minecraft."""
import json
import os
import re
import shlex
import shutil
import subprocess
import threading

from .config import APP_NAME, APP_VERSION
from .installer import collect_libraries, client_task, asset_info, game_assets_dir, logging_info, extract_natives
from .rules import is_allowed, os_name
from .versions import version_dir, version_jar_path

DEFAULT_JVM = [
    "-XX:+UnlockExperimentalVMOptions", "-XX:+UseG1GC", "-XX:G1NewSizePercent=20",
    "-XX:G1ReservePercent=20", "-XX:MaxGCPauseMillis=50", "-XX:G1HeapRegionSize=32M",
]

_VAR_RE = re.compile(r"\$\{([^}]+)\}")


def _subst(s, variables):
    return _VAR_RE.sub(lambda m: str(variables.get(m.group(1), m.group(0))), s)


def _expand(args, variables, features):
    out = []
    for a in args:
        if isinstance(a, str):
            out.append(_subst(a, variables))
        elif isinstance(a, dict):
            if not is_allowed(a.get("rules"), features):
                continue
            v = a.get("value")
            if isinstance(v, list):
                out.extend(_subst(x, variables) for x in v)
            elif isinstance(v, str):
                out.append(_subst(v, variables))
    return out


def is_modded(root, vid):
    try:
        import json
        with open(os.path.join(version_dir(root, vid), vid + ".json"), "r", encoding="utf-8") as f:
            return bool(json.load(f).get("inheritsFrom"))
    except (OSError, ValueError):
        return False


def instance_dir(settings, root, vid):
    """Сборки с модами всегда живут в своей папке, чтобы моды разных версий не смешивались."""
    if vid and (settings.get("separate_instances") or is_modded(root, vid)):
        return os.path.join(root, "instances", vid)
    return root


def _uses_var(args, name):
    """Есть ли в аргументах версии ${name} (например, quickPlayMultiplayer в 1.20+)."""
    return "${%s}" % name in json.dumps(args or [])


def own_client_jar(vdata, root, jar_path):
    """Forge и NeoForge 1.17+ сами подключают пропатченный Minecraft, а ванильный jar пропускают по имени:
    -DignoreList=...,${version_name}.jar. Поэтому клиент должен лежать в папке своей версии под её именем,
    как у официального лаунчера, иначе Java падает с «Modules minecraft and _1._20._1 export package»."""
    if "${version_name}.jar" not in json.dumps((vdata.get("arguments") or {}).get("jvm") or []):
        return jar_path
    own = version_jar_path(root, vdata["id"])
    if os.path.normcase(os.path.abspath(own)) == os.path.normcase(os.path.abspath(jar_path)):
        return jar_path
    if not os.path.isfile(own) or os.path.getsize(own) != os.path.getsize(jar_path):
        os.makedirs(os.path.dirname(own), exist_ok=True)
        shutil.copyfile(jar_path, own + ".part")
        os.replace(own + ".part", own)
    return own


def build_command(vdata, root, game_dir, java_exe, account, settings, demo=False, server=None):
    """Возвращает (cmd_list, natives_dir). Предполагается, что версия уже установлена.
    server=(host, port) — сразу подключиться к серверу (port 0 = по умолчанию / SRV)."""
    vid = vdata["id"]
    w, h = int(settings.get("width") or 0), int(settings.get("height") or 0)
    features = {
        "is_demo_user": bool(demo),
        "has_custom_resolution": bool(w and h),
    }
    quick_play = bool(server) and _uses_var((vdata.get("arguments") or {}).get("game"), "quickPlayMultiplayer")
    if quick_play:
        features["is_quick_play_multiplayer"] = True
    classpath, natives, _ = collect_libraries(vdata, root, features)
    jar_path, _ = client_task(vdata, root)
    classpath = classpath + [own_client_jar(vdata, root, jar_path)]
    natives_dir = os.path.join(version_dir(root, vid), "natives")
    extract_natives(natives, natives_dir)
    index_id, _, _ = asset_info(vdata, root)

    if account.get("type") != "microsoft" or not account.get("mc_token"):
        raise ValueError("Для игры нужен аккаунт Microsoft с лицензией Minecraft")
    token = account["mc_token"]
    uid = account.get("id", "").replace("-", "")
    variables = {
        "auth_player_name": account.get("name", "Player"),
        "version_name": vid,
        "game_directory": game_dir,
        "assets_root": os.path.join(root, "assets"),
        "game_assets": game_assets_dir(vdata, root, game_dir),
        "assets_index_name": index_id,
        "auth_uuid": uid,
        "auth_access_token": token,
        "auth_session": "token:%s:%s" % (token, uid),
        "auth_xuid": account.get("xuid", "0"),
        "clientid": "",
        "user_type": "msa",
        "user_properties": "{}",
        "version_type": vdata.get("type", "release"),
        "resolution_width": str(w),
        "resolution_height": str(h),
        "natives_directory": natives_dir,
        "launcher_name": APP_NAME,
        "launcher_version": APP_VERSION,
        "classpath": os.pathsep.join(classpath),
        "classpath_separator": os.pathsep,
        "library_directory": os.path.join(root, "libraries"),
    }
    if quick_play:
        host, port = server
        variables["quickPlayMultiplayer"] = host + (":%d" % port if port else "")

    jvm = ["-Xmx%dM" % int(settings.get("memory_max")), "-Xms%dM" % int(settings.get("memory_min"))]
    jvm += DEFAULT_JVM
    args = vdata.get("arguments") or {}
    if args.get("jvm"):
        jvm += _expand(args["jvm"], variables, features)
    else:
        if os_name() == "windows":
            jvm.append("-XX:HeapDumpPath=MojangTricksIntelDriversForPerformance_javaw.exe_minecraft.exe.heapdump")
        jvm += _expand([
            "-Djava.library.path=${natives_directory}",
            "-Dminecraft.launcher.brand=${launcher_name}",
            "-Dminecraft.launcher.version=${launcher_version}",
            "-cp", "${classpath}",
        ], variables, features)
    log_arg, _ = logging_info(vdata, root)
    if log_arg:
        jvm.append(log_arg)
    extra = (settings.get("jvm_args") or "").strip()
    if extra:
        if os_name() == "windows":
            parts = shlex.split(extra, posix=False)
            jvm += [p[1:-1] if len(p) >= 2 and p[0] == p[-1] == '"' else p for p in parts]
        else:
            jvm += shlex.split(extra)
    # -cp должен идти до mainClass, пользовательские аргументы — тоже
    game = []
    if args.get("game"):
        game = _expand(args["game"], variables, features)
    elif vdata.get("minecraftArguments"):
        game = [_subst(x, variables) for x in vdata["minecraftArguments"].split()]
        if w and h:
            game += ["--width", str(w), "--height", str(h)]
        if demo:
            game.append("--demo")
    if settings.get("fullscreen"):
        game.append("--fullscreen")
    if server and not quick_play:
        game += ["--server", server[0], "--port", str(server[1] or 25565)]

    cmd = [java_exe] + jvm + [vdata["mainClass"]] + game
    return cmd, natives_dir


def mask_cmd(cmd, account):
    tok = account.get("mc_token")
    out = []
    for c in cmd:
        if tok and tok in c:
            c = c.replace(tok, "***")
        out.append(c)
    return out


# ---------------------------------------------------------------- лог игры

_EVT_START = re.compile(r'<log4j:Event[^>]*logger="([^"]*)"[^>]*level="([^"]*)"[^>]*thread="([^"]*)"')
_CDATA = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.S)


class LogParser:
    """Превращает XML-события log4j (новые версии) в обычные строки."""

    def __init__(self):
        self.buf = None
        self.meta = None

    def feed(self, line):
        s = line.rstrip("\r\n")
        if self.buf is None:
            m = _EVT_START.search(s)
            if s.lstrip().startswith("<log4j:Event") and m:
                self.buf = [s]
                self.meta = m.groups()
                if "</log4j:Event>" in s:
                    return self._flush()
                return None
            return (s, "INFO")
        self.buf.append(s)
        if "</log4j:Event>" in s:
            return self._flush()
        return None

    def _flush(self):
        text = "\n".join(self.buf)
        logger, level, thread = self.meta
        self.buf, self.meta = None, None
        parts = _CDATA.findall(text)
        msg = "\n".join(p.strip("\n") for p in parts)
        return ("[%s/%s] %s" % (thread, level, msg), level)


def start_process(cmd, cwd, on_line, on_exit):
    os.makedirs(cwd, exist_ok=True)
    flags = 0x08000000 if os_name() == "windows" else 0  # CREATE_NO_WINDOW
    proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, creationflags=flags)

    def reader():
        parser = LogParser()
        for raw in iter(proc.stdout.readline, b""):
            try:
                line = raw.decode("utf-8")
            except UnicodeDecodeError:
                line = raw.decode("cp1251", "replace") if os_name() == "windows" else raw.decode("latin-1")
            res = parser.feed(line)
            if res:
                on_line(*res)
        code = proc.wait()
        on_exit(code)

    threading.Thread(target=reader, daemon=True).start()
    return proc
