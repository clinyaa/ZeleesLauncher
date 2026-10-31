"""Моды: поиск и скачивание (Modrinth, CurseForge), зависимости, проверка совместимости, папка mods."""
import json
import os
import re
import urllib.parse
import zipfile

from . import net
from .loaders import LOADER_NAMES, modrinth_loaders

MODRINTH = "https://api.modrinth.com/v2"
CURSEFORGE = "https://api.curseforge.com/v1"
CF_GAME = 432
CF_CLASS_MODS = 6
CF_LOADER = {"forge": 1, "fabric": 4, "quilt": 5, "neoforge": 6}
# виды контента каталога: мод, ресурс-пак, шейдер
CF_CLASS = {"mod": CF_CLASS_MODS, "resourcepack": 12, "shader": 6552}
CF_PAGE = {"mod": "mc-mods", "resourcepack": "texture-packs", "shader": "shaders"}
KIND_EXT = {"mod": ".jar", "resourcepack": ".zip", "shader": ".zip"}

INDEX_NAME = ".zelees-mods.json"
OLD_INDEX_NAME = ".mylauncher-mods.json"
BUILTIN_DEPS = {"minecraft", "java", "fabricloader", "fabric-loader", "quilt_loader", "quilt_base", "forge",
                "neoforge", "javafml", "lowcodefml", "mcp", "fml"}


class ModsError(Exception):
    def __init__(self, msg, url=None):
        super().__init__(msg)
        self.url = url


# =====================================================================================
#  Modrinth
# =====================================================================================
class Modrinth:
    name = "Modrinth"
    key = "modrinth"

    def _get(self, path, params=None):
        url = MODRINTH + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        return net.get_json(url, timeout=20)

    def search(self, query, mc, loader, offset=0, sort="downloads", limit=20, kind="mod"):
        facets = [["project_type:" + kind]]
        if mc:
            facets.append(["versions:" + mc])
        ls = modrinth_loaders(loader) if kind == "mod" else []
        if ls:
            facets.append(["categories:" + l for l in ls])
        r = self._get("/search", {"query": query or "", "facets": json.dumps(facets), "index": sort,
                                  "offset": offset, "limit": limit})
        hits = []
        for h in r.get("hits", []):
            hits.append({
                "source": self.key, "id": h["project_id"], "slug": h.get("slug"), "title": h.get("title", "?"),
                "author": h.get("author", ""), "description": h.get("description", ""),
                "downloads": h.get("downloads", 0), "icon_url": h.get("icon_url") or "",
                "categories": [c for c in h.get("categories", []) if c not in ("fabric", "forge", "quilt", "neoforge")],
                "page_url": "https://modrinth.com/%s/%s" % (kind, h.get("slug") or h["project_id"]),
                "client_side": h.get("client_side"),
            })
        return hits, r.get("total_hits", len(hits))

    @staticmethod
    def _to_file(v):
        files = v.get("files") or []
        f = next((x for x in files if x.get("primary")), files[0] if files else None)
        if not f:
            return None
        return {
            "source": "modrinth", "project_id": v["project_id"], "version_id": v["id"],
            "version_number": v.get("version_number", ""), "filename": f["filename"], "url": f["url"],
            "sha1": (f.get("hashes") or {}).get("sha1"), "size": f.get("size"),
            "game_versions": v.get("game_versions", []), "loaders": v.get("loaders", []),
            "deps": [(d.get("project_id"), d.get("version_id"), d.get("dependency_type"))
                     for d in v.get("dependencies", [])],
            "version_type": v.get("version_type", "release"), "other_mc": bool(v.get("_other_mc")),
        }

    def best_file(self, project_id, mc, loader, kind="mod"):
        params = {"game_versions": json.dumps([mc])}
        ls = modrinth_loaders(loader) if kind == "mod" else []
        if ls:
            params["loaders"] = json.dumps(ls)
        vs = self._get("/project/%s/version" % project_id, params)
        if not vs and kind != "mod":
            # ресурс-паки и шейдеры обычно работают и на соседних версиях игры — берём самую свежую
            vs = self._get("/project/%s/version" % project_id)
            for v in vs:
                v["_other_mc"] = True
        if not vs:
            return None
        # релиз лучше беты; для Quilt родные моды лучше Fabric-модов
        order = {"release": 0, "beta": 1, "alpha": 2}

        def rank(v):
            native = 0 if (loader in v.get("loaders", [])) else 1
            return (order.get(v.get("version_type"), 3), native)
        best = min(enumerate(vs), key=lambda iv: (rank(iv[1]), iv[0]))[1]
        return self._to_file(best)

    def version(self, version_id):
        return self._to_file(self._get("/version/%s" % version_id))

    def versions(self, ids):
        """version_id -> файл; несуществующие версии в ответ не попадают."""
        ids, res = list(ids), {}
        for i in range(0, len(ids), 100):
            for v in self._get("/versions", {"ids": json.dumps(ids[i:i + 100])}):
                mf = self._to_file(v)
                if mf:
                    res[v["id"]] = mf
        return res

    def project(self, pid):
        p = self._get("/project/%s" % pid)
        return {"source": self.key, "id": p["id"], "slug": p.get("slug"), "title": p.get("title"),
                "description": p.get("description", ""), "icon_url": p.get("icon_url") or "",
                "downloads": p.get("downloads", 0), "author": "", "categories": p.get("categories", []),
                "page_url": "https://modrinth.com/mod/%s" % p.get("slug", pid)}

    def projects(self, ids):
        if not ids:
            return {}
        r = self._get("/projects", {"ids": json.dumps(list(ids))})
        return {p["id"]: p for p in r}

    def identify(self, sha1s):
        if not sha1s:
            return {}
        r = net.post_json(MODRINTH + "/version_files", {"hashes": list(sha1s), "algorithm": "sha1"})
        return {h: self._to_file(v) for h, v in r.items()}

    def updates(self, sha1s, mc, loader):
        if not sha1s:
            return {}
        r = net.post_json(MODRINTH + "/version_files/update", {
            "hashes": list(sha1s), "algorithm": "sha1", "loaders": modrinth_loaders(loader), "game_versions": [mc]})
        return {h: self._to_file(v) for h, v in r.items()}


# =====================================================================================
#  CurseForge (нужен личный API-ключ: https://console.curseforge.com)
# =====================================================================================
class CurseForge:
    name = "CurseForge"
    key = "curseforge"

    def __init__(self, api_key):
        self.api_key = api_key.strip()

    def _get(self, path, params=None):
        url = CURSEFORGE + path
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
        try:
            return net.get_json(url, headers={"x-api-key": self.api_key, "Accept": "application/json"}, timeout=20)
        except net.HttpError as e:
            if e.status in (401, 403):
                raise ModsError("CurseForge отклонил API-ключ. Проверьте его в настройках.")
            raise

    def search(self, query, mc, loader, offset=0, sort="downloads", limit=20, kind="mod"):
        sort_field = {"downloads": 6, "relevance": 2, "updated": 3, "newest": 11}.get(sort, 2)
        r = self._get("/mods/search", {"gameId": CF_GAME, "classId": CF_CLASS[kind], "searchFilter": query,
                                       "gameVersion": mc,
                                       "modLoaderType": CF_LOADER.get(loader) if kind == "mod" else None,
                                       "sortField": sort_field, "sortOrder": "desc", "index": offset,
                                       "pageSize": limit})
        hits = []
        for m in r.get("data", []):
            hits.append({
                "source": self.key, "id": str(m["id"]), "slug": m.get("slug"), "title": m.get("name", "?"),
                "author": ", ".join(a.get("name", "") for a in m.get("authors", [])[:2]),
                "description": m.get("summary", ""), "downloads": int(m.get("downloadCount", 0)),
                "icon_url": ((m.get("logo") or {}).get("thumbnailUrl") or ""),
                "categories": [c.get("name", "") for c in m.get("categories", [])[:3]],
                "page_url": (m.get("links") or {}).get("websiteUrl") or "https://www.curseforge.com/minecraft/%s/%s" % (CF_PAGE[kind], m.get("slug")),
            })
        total = (r.get("pagination") or {}).get("totalCount", len(hits))
        return hits, total

    def _to_file(self, f, project_id):
        sha1 = next((h["value"] for h in f.get("hashes", []) if h.get("algo") == 1), None)
        gv = f.get("gameVersions", [])
        loaders = [x.lower() for x in gv if x.lower() in ("forge", "fabric", "quilt", "neoforge")]
        return {
            "source": self.key, "project_id": str(project_id), "version_id": str(f["id"]),
            "version_number": f.get("displayName", ""), "filename": f["fileName"], "url": f.get("downloadUrl"),
            "sha1": sha1, "size": f.get("fileLength"), "game_versions": [x for x in gv if x[:1].isdigit()],
            "loaders": loaders,
            "deps": [(str(d["modId"]), None, "required" if d.get("relationType") == 3 else
                      ("incompatible" if d.get("relationType") == 5 else "optional"))
                     for d in f.get("dependencies", [])],
            "version_type": {1: "release", 2: "beta", 3: "alpha"}.get(f.get("releaseType"), "release"),
        }

    def best_file(self, project_id, mc, loader, kind="mod"):
        r = self._get("/mods/%s/files" % project_id, {
            "gameVersion": mc, "modLoaderType": CF_LOADER.get(loader) if kind == "mod" else None, "pageSize": 50})
        files = r.get("data", [])
        if not files:
            return None
        files.sort(key=lambda f: (f.get("releaseType", 1), f.get("fileDate", "")), reverse=False)
        files.sort(key=lambda f: f.get("fileDate", ""), reverse=True)
        files.sort(key=lambda f: f.get("releaseType", 1))
        return self._to_file(files[0], project_id)

    def files(self, ids):
        """file_id -> файл (по id файлов CurseForge, одним запросом)."""
        ids, res = [int(x) for x in ids], {}
        for i in range(0, len(ids), 100):
            try:
                r = net.post_json(CURSEFORGE + "/mods/files", {"fileIds": ids[i:i + 100]},
                                  headers={"x-api-key": self.api_key}, timeout=20)
            except net.HttpError as e:
                if e.status in (401, 403):
                    raise ModsError("CurseForge отклонил API-ключ. Проверьте его в настройках.")
                raise
            for f in r.get("data", []):
                res[str(f["id"])] = self._to_file(f, f.get("modId"))
        return res

    def projects(self, ids):
        """project_id -> {"title", "icon_url", "page_url", "class_id"} (одним запросом на 100 проектов)."""
        ids, res = [int(x) for x in ids], {}
        for i in range(0, len(ids), 100):
            try:
                r = net.post_json(CURSEFORGE + "/mods", {"modIds": ids[i:i + 100]},
                                  headers={"x-api-key": self.api_key}, timeout=20)
            except net.HttpError as e:
                if e.status in (401, 403):
                    raise ModsError("CurseForge отклонил API-ключ. Проверьте его в настройках.")
                raise
            for m in r.get("data", []):
                res[str(m["id"])] = {"title": m.get("name", ""), "class_id": m.get("classId"),
                                     "icon_url": ((m.get("logo") or {}).get("thumbnailUrl") or ""),
                                     "page_url": (m.get("links") or {}).get("websiteUrl", "")}
        return res

    def project(self, pid):
        m = self._get("/mods/%s" % pid).get("data", {})
        return {"source": self.key, "id": str(m.get("id", pid)), "title": m.get("name", str(pid)),
                "description": m.get("summary", ""), "icon_url": ((m.get("logo") or {}).get("thumbnailUrl") or ""),
                "page_url": (m.get("links") or {}).get("websiteUrl", "")}


def providers(settings):
    res = [Modrinth()]
    key = (settings.get("curseforge_key") or "").strip()
    if key:
        res.append(CurseForge(key))
    return res


# =====================================================================================
#  Метаданные внутри jar (работает для модов с любых сайтов)
# =====================================================================================
def read_jar_meta(path):
    """-> dict(loader, ids, name, version, mc_constraint, deps[]) или None."""
    try:
        z = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile):
        return None
    with z:
        names = set(z.namelist())
        if "quilt.mod.json" in names:
            d = _json(z.read("quilt.mod.json"))
            ql = d.get("quilt_loader", {})
            meta = ql.get("metadata", {})
            deps, mc = [], None
            for dep in ql.get("depends", []):
                if isinstance(dep, str):
                    deps.append(dep)
                elif isinstance(dep, dict):
                    if dep.get("id") == "minecraft":
                        mc = ("fabric", dep.get("versions", "*"))
                    elif not dep.get("optional"):
                        deps.append(dep.get("id"))
            breaks = [(b, "fabric", "*") if isinstance(b, str) else (b.get("id"), "fabric", b.get("versions", "*"))
                      for b in ql.get("breaks", []) or [] if isinstance(b, (str, dict))]
            return {"loader": "quilt", "ids": [ql.get("id", "")] + [p if isinstance(p, str) else p.get("id", "")
                                                                   for p in ql.get("provides", [])],
                    "name": meta.get("name") or ql.get("id"), "version": ql.get("version", ""),
                    "mc": mc, "deps": [x for x in deps if x and x not in BUILTIN_DEPS],
                    "breaks": [b for b in breaks if b[0] and b[0] not in BUILTIN_DEPS]}
        if "fabric.mod.json" in names:
            d = _json(z.read("fabric.mod.json"))
            dep = d.get("depends") or {}
            mc = ("fabric", dep.get("minecraft")) if "minecraft" in dep else None
            return {"loader": "fabric", "ids": [d.get("id", "")] + list(d.get("provides", []) or []),
                    "name": d.get("name") or d.get("id"), "version": d.get("version", ""), "mc": mc,
                    "deps": [k for k in dep if k not in BUILTIN_DEPS],
                    "breaks": [(k, "fabric", v) for k, v in (d.get("breaks") or {}).items()
                               if k not in BUILTIN_DEPS]}
        for toml_name, loader in (("META-INF/neoforge.mods.toml", "neoforge"), ("META-INF/mods.toml", "forge")):
            if toml_name in names:
                t = _toml(z.read(toml_name).decode("utf-8", "replace"))
                mods = t.get("mods") or [{}]
                ids = [m.get("modId", "") for m in mods]
                deps, breaks, mc = [], [], None
                dep_tables = t.get("dependencies") or {}
                for _mid, lst in dep_tables.items():
                    for dd in lst if isinstance(lst, list) else []:
                        mid = dd.get("modId")
                        required = dd.get("mandatory", dd.get("type", "required") == "required")
                        if isinstance(required, str):
                            required = required.lower() == "required"
                        if str(dd.get("type", "")).lower() == "incompatible":
                            if mid and mid not in BUILTIN_DEPS:
                                breaks.append((mid, "maven", dd.get("versionRange", "")))
                        elif mid == "minecraft":
                            mc = ("maven", dd.get("versionRange", ""))
                        elif mid == "neoforge":
                            loader = "neoforge"
                        elif required and mid and mid not in BUILTIN_DEPS:
                            deps.append(mid)
                return {"loader": loader, "ids": ids, "name": mods[0].get("displayName") or ids[0],
                        "version": mods[0].get("version", ""), "mc": mc, "deps": deps, "breaks": breaks}
        if "mcmod.info" in names:
            d = _json(z.read("mcmod.info"))
            m = (d[0] if isinstance(d, list) and d else (d.get("modList") or [{}])[0] if isinstance(d, dict) else {})
            mcv = m.get("mcversion")
            return {"loader": "forge", "ids": [m.get("modid", "")], "name": m.get("name") or m.get("modid"),
                    "version": m.get("version", ""), "mc": ("exact", mcv) if mcv else None, "deps": []}
    return None


def _json(b):
    try:
        return json.loads(b.decode("utf-8", "replace"), strict=False)
    except ValueError:
        return {}


def _toml(text):
    try:
        import tomllib
        return tomllib.loads(text)
    except Exception:  # noqa: BLE001 - нет tomllib (Python < 3.11) или кривой файл
        return _mini_toml(text)


def _mini_toml(text):
    """Очень простой разбор mods.toml: [[mods]] и [[dependencies.x]]."""
    res, cur = {}, None
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip() if not line.strip().startswith(("'''", '"""')) else ""
        if not line:
            continue
        m = re.match(r"^\[\[([\w.\-]+)\]\]$", line)
        if m:
            path = m.group(1).split(".")
            cur = {}
            if path[0] == "dependencies" and len(path) > 1:
                res.setdefault("dependencies", {}).setdefault(path[1], []).append(cur)
            else:
                res.setdefault(path[0], []).append(cur)
            continue
        if line.startswith("["):
            cur = None
            continue
        m = re.match(r'^([\w\-]+)\s*=\s*(.+)$', line)
        if m:
            k, v = m.group(1), m.group(2).strip()
            if v.startswith(('"', "'")):
                v = v[1:].split(v[0], 1)[0]
            elif v in ("true", "false"):
                v = v == "true"
            (cur if cur is not None else res)[k] = v
    return res


# ---------------------------------------------------------------- сравнение версий

def _vt(s):
    s = s.strip()
    if not re.match(r"^\d+(\.\d+)*$", s):
        return None
    return tuple(int(x) for x in s.split("."))


def _cmp(a, b):
    n = max(len(a), len(b))
    a, b = a + (0,) * (n - len(a)), b + (0,) * (n - len(b))
    return (a > b) - (a < b)


def _fabric_pred(pred, mc):
    pred = pred.strip()
    if pred in ("*", ""):
        return True
    m = re.match(r"^(>=|<=|>|<|=|~|\^)?\s*(.+)$", pred)
    op, ver = m.group(1) or "=", m.group(2).strip()
    if ver.endswith((".x", ".X", ".*")):
        base = _vt(ver[:-2])
        return base is not None and mc[:len(base)] == base
    v = _vt(ver.split("-")[0].split("+")[0])
    if v is None:
        return None
    c = _cmp(mc, v)
    if op == "=":
        return c == 0
    if op == ">=":
        return c >= 0
    if op == "<=":
        return c <= 0
    if op == ">":
        return c > 0
    if op == "<":
        return c < 0
    if op == "~":
        hi = v[:2] if len(v) >= 2 else v + (0,)
        hi = (hi[0], hi[1] + 1)
        return c >= 0 and _cmp(mc, hi) < 0
    if op == "^":
        return c >= 0 and _cmp(mc, (v[0] + 1,)) < 0
    return None


def _fabric_match(spec, mc):
    if spec is None:
        return None
    specs = spec if isinstance(spec, list) else [spec]
    unknown = False
    for s in specs:  # список = ИЛИ
        ok = True
        for pred in str(s).split():  # пробел = И
            r = _fabric_pred(pred, mc)
            if r is None:
                unknown = True
                ok = False
                break
            if not r:
                ok = False
                break
        if ok:
            return True
    return None if unknown else False


def _maven_match(spec, mc):
    spec = (spec or "").strip()
    if not spec:
        return None
    if spec[0] not in "[(":
        v = _vt(spec)
        return None if v is None else _cmp(mc, v) == 0 or None  # «мягкая» версия — не запрещает
    ranges = re.findall(r"[\[(][^\])]*[\])]", spec)
    unknown = False
    for r in ranges:
        lo_inc, hi_inc = r[0] == "[", r[-1] == "]"
        body = r[1:-1]
        if "," not in body:
            v = _vt(body)
            if v is None:
                unknown = True
                continue
            if _cmp(mc, v) == 0:
                return True
            continue
        lo, hi = [x.strip() for x in body.split(",", 1)]
        ok = True
        if lo:
            v = _vt(lo)
            if v is None:
                unknown = True
                continue
            c = _cmp(mc, v)
            ok = ok and (c >= 0 if lo_inc else c > 0)
        if hi:
            v = _vt(hi)
            if v is None:
                unknown = True
                continue
            c = _cmp(mc, v)
            ok = ok and (c <= 0 if hi_inc else c < 0)
        if ok:
            return True
    return None if unknown else False


def jar_compat(meta, mc, loader):
    """-> (True/False/None, причина)."""
    if not meta:
        return None, "не удалось прочитать мод"
    ml = meta["loader"]
    ok_loader = (ml == loader or (loader == "quilt" and ml == "fabric")
                 or (loader == "neoforge" and ml == "forge" and mc == "1.20.1"))
    if not ok_loader:
        return False, "мод для %s, а сборка на %s" % (LOADER_NAMES.get(ml, ml), LOADER_NAMES.get(loader, loader))
    mct = _vt(mc)
    if not meta.get("mc") or mct is None:
        return None, "версия Minecraft не указана в моде"
    kind, spec = meta["mc"]
    if kind == "fabric":
        r = _fabric_match(spec, mct)
    elif kind == "maven":
        r = _maven_match(spec, mct)
    else:
        r = (str(spec).strip() == mc)
    if r is False:
        return False, "мод рассчитан на Minecraft %s" % _pretty_spec(spec)
    if r is None:
        return None, "не удалось проверить версию (%s)" % _pretty_spec(spec)
    return True, "совместим"


def _pretty_spec(spec):
    if isinstance(spec, list):
        return " или ".join(str(s) for s in spec)
    return str(spec)


# =====================================================================================
#  Папка mods
# =====================================================================================
class ModFolder:
    """Папка mods (или resourcepacks/shaderpacks с ext=".zip") и индекс скачанных лаунчером файлов."""

    def __init__(self, path, ext=".jar"):
        self.path = path
        self.ext = ext
        self.index_path = os.path.join(path, INDEX_NAME)
        src = self.index_path
        if not os.path.isfile(src) and os.path.isfile(os.path.join(path, OLD_INDEX_NAME)):
            src = os.path.join(path, OLD_INDEX_NAME)  # индекс от старой версии лаунчера
        try:
            with open(src, "r", encoding="utf-8") as f:
                self.index = json.load(f)
        except (OSError, ValueError):
            self.index = {}

    def save_index(self):
        os.makedirs(self.path, exist_ok=True)
        with open(self.index_path, "w", encoding="utf-8") as f:
            json.dump(self.index, f, ensure_ascii=False, indent=1)

    @staticmethod
    def base_name(fn):
        return fn[:-len(".disabled")] if fn.endswith(".disabled") else fn

    def entries(self):
        if not os.path.isdir(self.path):
            return []
        res = []
        for fn in sorted(os.listdir(self.path), key=str.lower):
            if not (fn.endswith(self.ext) or fn.endswith(self.ext + ".disabled")):
                continue
            p = os.path.join(self.path, fn)
            base = self.base_name(fn)
            res.append({"file": fn, "base": base, "path": p, "enabled": not fn.endswith(".disabled"),
                        "meta": self.index.get(base, {})})
        return res

    def installed_projects(self):
        """project_id -> имя файла (для пометки «установлен» в каталоге)."""
        res = {}
        for e in self.entries():
            pid = e["meta"].get("project_id")
            if pid:
                res[(e["meta"].get("source"), pid)] = e["file"]
        return res

    def install(self, mf, title=None, icon_url=None, cancel=None):
        if not mf.get("url"):
            raise ModsError("Автор мода запретил скачивание из сторонних лаунчеров — скачайте его со страницы мода "
                            "и положите в папку mods.", mf.get("page_url"))
        os.makedirs(self.path, exist_ok=True)
        dest = os.path.join(self.path, mf["filename"])
        net.download(mf["url"], dest, mf.get("sha1"), cancel=cancel)
        # удаляем старые файлы этого же мода (обновление)
        for e in self.entries():
            m = e["meta"]
            if e["base"] != mf["filename"] and m.get("project_id") == mf["project_id"] and \
                    m.get("source") == mf["source"]:
                self.delete(e["file"])
        self.index[mf["filename"]] = {
            "source": mf["source"], "project_id": mf["project_id"], "version_id": mf["version_id"],
            "version_number": mf.get("version_number", ""), "title": title or mf["filename"],
            "icon_url": icon_url or "", "game_versions": mf.get("game_versions", []),
            "loaders": mf.get("loaders", []),
        }
        self.save_index()
        return dest

    def update(self, old_file, mf, title=None, icon_url=None, cancel=None):
        """Ставит новую версию мода вместо old_file. Отключённый мод остаётся отключённым."""
        was_disabled = old_file.endswith(".disabled")
        self.install(mf, title, icon_url, cancel)
        old = os.path.join(self.path, old_file)
        if old_file != mf["filename"] and os.path.exists(old):
            os.remove(old)
            if self.base_name(old_file) != mf["filename"]:
                self.index.pop(self.base_name(old_file), None)
                self.save_index()
        if was_disabled:
            self.toggle(mf["filename"])

    def delete(self, fn):
        try:
            os.remove(os.path.join(self.path, fn))
        except OSError:
            pass
        self.index.pop(self.base_name(fn), None)
        self.save_index()

    def toggle(self, fn):
        src = os.path.join(self.path, fn)
        dst = src[:-len(".disabled")] if fn.endswith(".disabled") else src + ".disabled"
        os.replace(src, dst)


# =====================================================================================
#  Зависимости и проверка установленных
# =====================================================================================
def resolve_install(provider, mf, mc, loader, installed, log=None, depth=0, plan=None, seen=None):
    """Возвращает список файлов к установке: сам мод + обязательные зависимости (рекурсивно)."""
    plan = [] if plan is None else plan
    seen = set() if seen is None else seen
    key = (mf["source"], mf["project_id"])
    if key in seen:
        return plan
    seen.add(key)
    plan.append(mf)
    if depth > 6:
        return plan
    for pid, vid, dtype in mf.get("deps", []):
        if dtype != "required" or not (pid or vid):
            continue
        if pid and (mf["source"], pid) in installed:
            continue
        dep = None
        try:
            if pid:
                dep = provider.best_file(pid, mc, loader)
            if dep is None and vid and hasattr(provider, "version"):
                dep = provider.version(vid)
        except Exception as e:  # noqa: BLE001
            if log:
                log("Не удалось получить зависимость %s: %s" % (pid or vid, e))
        if dep is None:
            raise ModsError("Не найдена совместимая версия обязательной зависимости (%s) для %s %s"
                            % (pid or vid, LOADER_NAMES.get(loader, loader), mc))
        if (dep["source"], dep["project_id"]) in installed:
            continue
        resolve_install(provider, dep, mc, loader, installed, log, depth + 1, plan, seen)
    return plan


def check_folder(folder, mc, loader, online=True, metas=None):
    """Проверяет все моды в папке. Возвращает список dict с полями:
    file, enabled, title, version, status ('ok'|'bad'|'unknown'), reason, update (mf|None), missing [ids].
    metas (dict) — если передан, в него кладутся метаданные jar по имени файла."""
    ents = folder.entries()
    sha = {}
    for e in ents:
        try:
            sha[e["file"]] = net.sha1_file(e["path"])
        except OSError:
            pass
    ident, upd = {}, {}
    if online and sha:
        mr = Modrinth()
        try:
            ident = mr.identify(set(sha.values()))
        except Exception:  # noqa: BLE001
            ident = {}
        try:
            upd = mr.updates(set(sha.values()), mc, loader)
        except Exception:  # noqa: BLE001
            upd = {}

    results, all_ids, mr_projects = [], set(), set()
    metas = {} if metas is None else metas
    for e in ents:
        meta = read_jar_meta(e["path"])
        metas[e["file"]] = meta
        if meta and e["enabled"]:
            all_ids.update(i for i in meta.get("ids", []) if i)
        v = ident.get(sha.get(e["file"]))
        if v and e["enabled"]:
            mr_projects.add(v["project_id"])
    for e in ents:
        meta = metas[e["file"]]
        h = sha.get(e["file"])
        v = ident.get(h)
        im = e["meta"]
        title = im.get("title") or (meta or {}).get("name") or e["base"]
        version = im.get("version_number") or (v or {}).get("version_number") or (meta or {}).get("version", "")
        status, reason = "unknown", "нет данных о совместимости"
        if v:
            gv_ok = mc in v.get("game_versions", [])
            ld_ok = any(l in v.get("loaders", []) for l in modrinth_loaders(loader))
            if gv_ok and ld_ok:
                status, reason = "ok", "совместим"
            elif not ld_ok:
                status, reason = "bad", "мод для %s" % ", ".join(LOADER_NAMES.get(l, l) for l in v.get("loaders", []))
            else:
                gvs = v.get("game_versions", [])
                status, reason = "bad", "только для %s" % (", ".join(gvs[-4:]) if gvs else "других версий")
        if status == "unknown" and meta:
            r, why = jar_compat(meta, mc, loader)
            status = {True: "ok", False: "bad", None: "unknown"}[r]
            reason = why
        elif status == "unknown" and im.get("game_versions"):
            ok = mc in im["game_versions"] and (not im.get("loaders") or
                                                any(l in im["loaders"] for l in modrinth_loaders(loader)))
            status, reason = ("ok", "совместим") if ok else ("bad", "только для %s" % ", ".join(im["game_versions"][-4:]))
        missing = []  # [{"name":..., "pid": project_id Modrinth или None}]
        if v and e["enabled"]:
            for pid, _vid, dtype in v.get("deps", []):
                if dtype == "required" and pid and pid not in mr_projects:
                    missing.append({"name": pid, "pid": pid})
        # зависимости из jar — только для модов не с Modrinth: у Modrinth-модов список точнее (в jar видны
        # и модули внутри Fabric API, которых нет среди установленных файлов)
        if meta and e["enabled"] and not v:
            missing = [{"name": d, "pid": None} for d in meta.get("deps", []) if d not in all_ids]
        u = upd.get(h)
        update = u if (u and v and u["version_id"] != v["version_id"]) else None
        results.append({"file": e["file"], "enabled": e["enabled"], "title": title, "version": version,
                        "status": status, "reason": reason, "update": update, "missing": missing,
                        "project_id": (v or {}).get("project_id") or im.get("project_id"),
                        "source": "modrinth" if v else im.get("source"), "icon_url": im.get("icon_url", "")})
    # человеческие названия для недостающих зависимостей с Modrinth
    pids = {m["pid"] for r in results for m in r["missing"] if m["pid"]}
    if pids and online:
        try:
            pr = Modrinth().projects(pids)
            for r in results:
                for m in r["missing"]:
                    if m["pid"] in pr:
                        m["name"] = pr[m["pid"]].get("title", m["name"])
        except Exception:  # noqa: BLE001
            pass
    return results


def _mod_vt(ver):
    """Версия мода без суффиксов («0.5.8+mc1.21.1» -> (0, 5, 8)) или None."""
    m = re.match(r"\d+(?:\.\d+)*", (ver or "").strip())
    return tuple(int(x) for x in m.group(0).split(".")) if m else None


def preflight(path, mc, loader):
    """Проверка папки mods перед запуском, без интернета: -> список проблем (строки).
    Несовместимые моды, недостающие зависимости, один мод в двух файлах, моды, которые явно ломают друг друга."""
    metas = {}
    res = check_folder(ModFolder(path), mc, loader, online=False, metas=metas)
    problems = []
    for r in res:
        if not r["enabled"]:
            continue
        if r["status"] == "bad":
            problems.append("«%s» — %s" % (r["title"], r["reason"]))
        if r["missing"]:
            problems.append("«%s»: не хватает %s" % (r["title"], ", ".join(m["name"] for m in r["missing"])))
    enabled = {r["file"] for r in res if r["enabled"]}
    on = {f: m for f, m in metas.items() if f in enabled and m}
    files, have = {}, {}
    for f, m in sorted(on.items()):
        ids = [i for i in m.get("ids") or [] if i]
        if ids:
            files.setdefault(ids[0], []).append(f)
        for i in ids:
            have.setdefault(i, (m.get("version", ""), m.get("name") or i))
    for mid, fs in sorted(files.items()):
        if len(fs) > 1:
            problems.append("«%s» установлен %d раза: %s" % (on[fs[0]].get("name") or mid, len(fs), ", ".join(fs)))
    for f, m in sorted(on.items()):
        for bid, kind, spec in m.get("breaks") or []:
            if bid not in have or bid in (m.get("ids") or []):
                continue
            ver, name = have[bid]
            if isinstance(spec, list):
                spec = spec or "*"
            if spec in ("*", "", None):
                hit = True
            else:
                vt = _mod_vt(ver)
                hit = vt is not None and (_fabric_match(spec, vt) if kind == "fabric" else _maven_match(spec, vt))
            if hit:
                problems.append("«%s» несовместим с «%s» %s" % (m.get("name") or f, name, ver))
    return problems
