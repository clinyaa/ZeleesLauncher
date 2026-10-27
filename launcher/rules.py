"""Платформа, правила (rules) из version.json и пути Maven."""
import platform
import re
import struct
import sys


def os_name():
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "osx"
    return "linux"


def is_64bit_os():
    m = platform.machine().lower()
    if m in ("amd64", "x86_64", "arm64", "aarch64"):
        return True
    return struct.calcsize("P") == 8


def os_arch():
    """Архитектура в терминах правил Mojang: x86 (32 бит), x86_64, arm64."""
    m = platform.machine().lower()
    if m in ("arm64", "aarch64"):
        return "arm64"
    if is_64bit_os():
        return "x86_64"
    return "x86"


def arch_bits():
    return "64" if is_64bit_os() else "32"


def os_version():
    if os_name() == "windows":
        v = platform.version()  # 10.0.19045
        return v
    return platform.release()


def rule_matches(rule, features):
    o = rule.get("os")
    if o:
        if "name" in o and o["name"] != os_name():
            return False
        if "arch" in o and o["arch"] != os_arch():
            return False
        if "version" in o:
            try:
                if not re.search(o["version"], os_version()):
                    return False
            except re.error:
                return False
    feats = rule.get("features")
    if feats:
        for k, v in feats.items():
            if bool(features.get(k, False)) != bool(v):
                return False
    return True


def is_allowed(rules, features=None):
    if not rules:
        return True
    features = features or {}
    allowed = False
    for r in rules:
        if rule_matches(r, features):
            allowed = r.get("action", "allow") == "allow"
    return allowed


def parse_maven(name):
    """group:artifact:version[:classifier][@ext] -> (group, artifact, version, classifier, ext)."""
    ext = "jar"
    if "@" in name:
        name, ext = name.rsplit("@", 1)
    parts = name.split(":")
    group, artifact, version = parts[0], parts[1], parts[2] if len(parts) > 2 else ""
    classifier = parts[3] if len(parts) > 3 else None
    return group, artifact, version, classifier, ext


def maven_path(name, classifier=None):
    group, artifact, version, cls, ext = parse_maven(name)
    cls = classifier or cls
    fname = "%s-%s%s.%s" % (artifact, version, ("-" + cls) if cls else "", ext)
    return "/".join(group.split(".") + [artifact, version, fname])


def library_key(name):
    group, artifact, _v, cls, _e = parse_maven(name)
    return "%s:%s:%s" % (group, artifact, cls or "")
