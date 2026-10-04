"""汇总本地已安装 skill 在 SkillHub / ClawHub 的发布状态。

SkillHub 判据： signature 端点 200 = 该版本已公开。
             search 精确命中但 signature 404 = 入库了但本地版本更新（待发新版）。
             两者皆无 = 从未发布。
ClawHub 判据： inspect --versions能列出该 slug。
用法： python out/_skill_status_matrix.py
"""
import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request

SH_CRED = r"C:\Users\DELL\.skillhub\credentials.json"
CH_DIR = r"C:\Users\DELL\AppData\Roaming\clawhub"
CH_CLI = r"C:\Users\DELL\.workbuddy\binaries\node\workspace\node_modules\clawhub\bin\clawdhub.js"
NODE = r"C:\Users\DELL\.workbuddy\binaries\node\versions\22.22.2-6\node.exe"
ROOT = r"C:\Users\DELL\.workbuddy\skills"

VERSION_RE = re.compile(r"^version:\s*[\"']?([^\s\"']+)", re.M)


def sh_sig(host, token, slug, ver):
    url = "{}/api/v1/open/skills/{}/versions/{}/signature".format(
        host, urllib.parse.quote(slug, safe=""), urllib.parse.quote(ver, safe="")
    )
    req = urllib.request.Request(
        url, headers={"Authorization": "Bearer " + token, "User-Agent": "skillhub-cli/2026.8.5"}
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:  # noqa: BLE001
        return "ERR"


def sh_search(host, token, slug):
    url = "{}/api/v1/search?{}".format(host, urllib.parse.urlencode({"q": slug, "limit": 20}))
    req = urllib.request.Request(
        url, headers={"Authorization": "Bearer " + token, "User-Agent": "skillhub-cli/2026.8.5"}
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        return None
    res = d.get("results") or []
    exact = [x for x in res if isinstance(x, dict) and x.get("slug") == slug]
    if not exact:
        return None
    return max((str(x.get("version") or "") for x in exact),
               key=lambda s: [int(p) if p.isdigit() else 0 for p in s.split(".")] or [0])


def ch_versions(slug):
    try:
        p = subprocess.run([NODE, CH_CLI, "inspect", "@oracis/" + slug, "--versions", "--limit", "50"],
                           capture_output=True, text=True, timeout=90)
    except Exception:  # noqa: BLE001
        return "ERR"
    out = (p.stdout or "") + (p.stderr or "")
    if "not publicly visible" in out:
        return "HIDDEN"
    if "not found" in out.lower():
        return "NONE"
    if "reset in" in out and "not found" not in out.lower():
        return "RATELIMIT"
    vers = sorted(set(re.findall(r"\b(\d+\.\d+\.\d+)\b", out)))
    return ",".join(vers) if vers else "?"


def main():
    sh = json.load(open(SH_CRED, encoding="utf-8"))["user"]
    rows = []
    for name in sorted(os.listdir(ROOT)):
        md = os.path.join(ROOT, name, "SKILL.md")
        if not os.path.isfile(md):
            continue
        txt = open(md, encoding="utf-8", errors="replace").read()
        m = VERSION_RE.search(txt)
        rows.append((name, m.group(1) if m else ""))

    print("{:<34} {:<8} {:<10} {:<12} {}".format("skill", "local", "SH_sig", "SH_remote", "CH_versions"))
    print("-" * 84)
    for name, ver in rows:
        if not ver:
            print("{:<34} {:<8} {:<10} {:<12} {}".format(name, "-", "NO_VER", "-", "-"))
            continue
        sig = sh_sig(sh["host"], sh["token"], name, ver)
        remote = sh_search(sh["host"], sh["token"], name)
        remote_s = remote if remote else "-"
        print("{:<34} {:<8} {:<10} {:<12} {}".format(name, ver, sig, remote_s, ch_versions(name)))


if __name__ == "__main__":
    main()