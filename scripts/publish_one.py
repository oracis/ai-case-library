#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一条命令走完「选平台 → 选稿 → 过闸 → 发布 → 回读 → 台账」。

为什么有它
----------
十平台（B站一号+二号）的发布能力早就有了，但**每次都重头攒一遍**：先跑 `publish_slot.py` 看今天
该发哪一类，再自己判断该用哪个脚本、带哪些参数、要不要先跑 katex/合规/去导流、
发完去哪个台账查是不是真落了。判断散在 9 个脚本的 argparse 和各篇
`PLAYBOOK-*.md` 里，一次发布要读四五个文件 —— 这就是这个入口要收掉的东西。

它**不重写任何平台逻辑**：CDP 选择器、封面注入、草稿箱点击全留在原脚本里
（那是几个月的踩坑沉淀）。这里只做三件事：

1. 把「今天这个平台该发哪篇」交给 `publish_slot.decide()`（唯一档期事实源）；
2. 把「发布前必过的闸」串成一条固定流水线（缺一条就是事故，见下表）；
3. 把「哪个平台用哪条命令、发完去哪回读」固化成一张表（`SPEC`）。

## 闸（缺一条都是真出过的事故）

| 闸 | 拦什么 | 谁提供 |
|---|---|---|
| `dedupe` | 该 case 在本平台已发过 / 已有远端草稿 / 长尾台账已有建稿批次 | `publish_guard.check_dupe()` |
| `draft` | 稿子产物不存在（没稿不许发） | 本文件 |
| `link` | 自家域名/索引站口气/引流话术 | `link_hygiene.scan()` |
| `katex` | KaTeX 平台 `$` 把数字吃成公式 | `katex_money_fix.scan_file()` |
| `tt` | 头条社区规范 block 级命中 | `tt_compliance.scan()` |
| `genre` | 腾讯云只收技术向：标题带金额/MRR 必被驳 | 本文件 |
| `title` | 标题超平台上限 / 硬截断 / 空壳模板 / AI 套话 / 金额与 `$` | `publish_guard.scan_title()` |
| `template` | 管线水印 / 罐头来源句 / 每篇同一串栏目 / 知乎导流 | `publish_guard.scan_content()` |
| `cover` | 本地无封面资源，或发布命令没带 `--cover` | `publish_guard.check_cover()` |
| `remote` | 两朵云没有远端草稿 id（还没上传过） | `data/yun_finalize.json` |

⚠ `cover` 闸只保证「本地有资源 + 命令会带参数」；**平台侧封面是否真的落库**
必须按 `VERIFY_HINT` 回读（腾讯云 `tc_manage.py status`、CSDN `csdn_list_drafts.py`）。
本地有 PNG ≠ 封面已设置 —— 2026-11-04 CSDN 三篇草稿就是这么裸着进去的。

## 用法

    python scripts/publish_one.py                     # 今日计划（只读，不碰浏览器）
    python scripts/publish_one.py --json              # 机器可读
    python scripts/publish_one.py --platform csdn     # 该平台今天该发的那篇：先干跑
    python scripts/publish_one.py --platform csdn --yes    # 真发
    python scripts/publish_one.py --platform tencentcloud --case chessigma --yes
    python scripts/publish_one.py --all --yes         # 今天该发的平台全发（串行）

退出码：0 = 计划/成功；1 = 被闸拦下或发布失败；3 = 参数错误。

⚠ 默认**干跑**：不加 `--yes` 只打印闸结果和将要执行的命令。真发一律逐平台来，
别拿一批去赌 —— 各平台的限额与审核是独立账本，一起失败时说不清是谁的锅。
"""

import argparse
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
DATA = os.path.join(ROOT, "data")
sys.path.insert(0, SCRIPTS)

import atomicio                                                          # noqa: E402
import db  # noqa: E402  SQLite 统一台账：两朵云发布记录读 platform_posts (platform='tencentcloud'/'aliyun')

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import csdn_rules as CR          # noqa: E402
import katex_money_fix as KX      # noqa: E402
import link_hygiene as LH         # noqa: E402
import publish_guard as G         # noqa: E402  幂等/标题/形态/封面 单一判据源（只读）
import publish_slot as SLOT       # noqa: E402
import tt_compliance as TT        # noqa: E402

PY = sys.executable or "python"

# 游戏仓：游戏稿的平台产物在那边（一期 10 份 platform/*.md）。跨仓引用只此一处，
# 用环境变量可覆盖（CI/换机时不必改代码）。
# ⚠ 真正的跨仓判据在 publish_slot.GAMES_ROOT（pack_of 在那里）；这里只用于
# 把路径显示成「游戏仓:xxx」，两处默认值必须一致。
GAMES_ROOT = os.environ.get("GAMES_ROOT", r"D:/open-source-games")

# --gen 可按需生成 AI 稿的平台（= gen_case_drafts.PLATFORMS，腾讯云/阿里云技术向
# 稿另有流程不在内）。注意 aliyun 在 gen 里能出「商业拆解」骨架，但要走
# 腾讯云体裁口径时仍需人工技术向重写 —— 现在阿里云过审口径与博客园一致，先放行。
GEN_PLATFORMS = ("cnblogs", "zhihu", "csdn", "aliyun")
GEN_SCRIPT = os.path.join(SCRIPTS, "gen_case_drafts.py")

# 平台 → 平台稿文件名（游戏仓 content/<slug>/platform/<file>）
GAME_FILE = {
    "wechat": "wechat.md", "toutiao": "toutiao.md", "xhs": "xhs.md",
    "bilibili": "bilibili.md", "cnblogs": "cnblogs.md", "zhihu": "zhihu.md",
    "csdn": "csdn.md", "tencentcloud": "tencentcloud.md", "aliyun": "aliyun.md",
    # 🚨 二号**发布时不读这个文件**：它走 bridge 从免费变体（article-free.md）
    #   生成 `out/articles/<cid>-b2.html`。这里登记只为「平台表不留洞」
    #   （test_publish_one 要求每平台四张表齐全），改了它不影响二号发布。
    "bilibili2": "bilibili2.md",
}

# 发布终点：draft = 脚本只能到草稿（后台还得人点一下），published = 真发表
TERMINAL = {
    # FIFO 默认路径是 send（真发表，但需管理员微信扫码）；只有 --new 新写才是草稿。
    "wechat": "published",
    "toutiao": "published",
    "xhs": "published",
    "bilibili": "published",
    "bilibili2": "published",   # 走游戏仓 bridge，发表完自己回读补账
    "cnblogs": "published",
    "zhihu": "draft",        # publish 端点未开放（见 zhihu_publish.py）
    "csdn": "published",
    "tencentcloud": "published",
    "aliyun": "published",
}

# 每平台必过的闸（顺序即执行顺序）
# 🚨 2026-11-04：`dedupe` / `title` / `template` / `cover` 补到**全部平台**。
#   2026-11-06 加入 B站二号（bilibili2），闸与一号同一套。
#   此前只有 4 个平台有 cover 闸、0 个平台有 case 级去重，B站/小红书的
#   FIFO 分支更是一条闸都不进 —— 重复草稿与裸图就是从这两处漏出去的。
GATES = {
    "wechat": ["dedupe", "draft", "link", "title", "template", "cover",
               "cover_remote"],
    "toutiao": ["dedupe", "draft", "link", "tt", "title", "template", "cover",
                "cover_remote"],
    "xhs": ["dedupe", "draft", "link", "title", "template", "cover",
            "cover_remote"],
    "bilibili": ["dedupe", "draft", "link", "title", "template", "cover",
                 "cover_remote"],
    "bilibili2": ["dedupe", "draft", "link", "title", "template", "cover",
                  "cover_remote"],
    "cnblogs": ["dedupe", "draft", "link", "title", "template", "cover",
                "cover_remote"],
    "zhihu": ["dedupe", "draft", "link", "title", "template", "cover",
              "cover_remote"],
    "csdn": ["dedupe", "draft", "link", "katex", "rules", "title", "template",
             "cover", "cover_remote"],
    "tencentcloud": ["dedupe", "draft", "link", "katex", "genre", "title",
                     "template", "cover", "cover_remote", "remote"],
    "aliyun": ["dedupe", "draft", "link", "katex", "title", "template", "cover",
               "cover_remote", "remote"],
}

# 缺产物时的补建命令（确定性产物才有：排版/二版图，不含 AI 改写）
BUILD = {
    "toutiao": lambda case: ["toutiao_publish.py", "build", "--case", case],
    "bilibili": lambda case: ["bilibili_publish.py", "build", "--case", case],
    "xhs": lambda case: ["xhs_publish.py", "build", "--case", case],
}

# 回读：发完去哪查「是不是真落了」
VERIFY_HINT = {
    "wechat": "FIFO：已发表列表按标题命中 + 台账 status=published（发布模式需扫码）；"
              "--new 路径只到草稿，群发仍要后台点",
    "toutiao": "「已发表」列表按标题命中才算成功（内容管理里的草稿不算）",
    "xhs": "data/xhs_published.json 按标题；创作中心笔记列表复核",
    "bilibili": "data/bili_published.json + 创作中心稿件列表",
    "bilibili2": "游戏仓 data/publish_state.json 的 bilibili2 列 + 二号创作中心稿件"
                 "列表按标题（aid 会重分配，不按 aid 判）",
    "cnblogs": "python scripts/cnblogs_publish.py list --n 5",
    "zhihu": "只能到草稿：zhihu_drafts.json 有记录即成功，发表待人工",
    "csdn": "python scripts/csdn_publish.py list --query <标题片段>",
    "tencentcloud": "脚本内已校验封面落库；python scripts/tc_manage.py status",
    "aliyun": "data/yun_finalize.json 的 al_draft；公开文章页复核",
}

MONEY_WORDS = ("MRR", "mrr", "月入", "月收入", "营收", "ARR", "美金", "美元",
               "$", "¥", "赚了", "变现", "付费率")


# ---------------------------------------------------------------- 台账 / 稿件

def _load(name):
    p = os.path.join(DATA, name)
    try:
        return atomicio.read_json(p, default={})
    except (OSError, ValueError):
        return {}


def pack_path(platform, case):
    """该平台这一篇的产物路径；没有就 None。

    判据只有一份：`publish_slot.pack_of()`（AI case 与游戏稿、本地与游戏仓
    两条来源都在那儿统一）。这里不另写一套，免得档期说「有稿」、发布说「没有」。
    """
    return SLOT.pack_of(platform, case) if case else None


def _html_of(path):
    """所有闸都作用于「真正要发出去的正文」：公众号发 HTML，其余发 markdown。"""
    if not path:
        return "", False
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            raw = f.read()
    except OSError:
        return "", False
    if path.lower().endswith((".html", ".htm")):
        return raw, True
    return raw, False


def cover_of(case):
    """封面资源查找链委托给 `publish_guard.cover_of()`（单一真理）。

    旧实现只查 `out/toutiao/<case>/cover.*`，而头条题图其实是
    `out/xhs/<case>/card-1.png`，漏掉就会把「有封面」误判成「没有」。
    """
    return G.cover_of(case)


# ---------------------------------------------------------------- 闸

def _gate_draft(platform, case, pack, **kw):
    if pack:
        return True, pack.replace(ROOT + os.sep, "").replace(GAMES_ROOT + "/", "游戏仓:")
    b = BUILD.get(platform)
    if b:
        return False, "缺产物，可补建：python scripts/%s" % " ".join(b(case))
    return False, ("缺产物且无确定性补建路径（正文要 AI 写）⇒ 先补稿："
                   "drafts/newbatch/%s/%s.md 或游戏仓 platform/%s"
                   % (platform, case, GAME_FILE.get(platform, "?")))


def _gate_link(platform, case, pack, **kw):
    text, is_html = _html_of(pack)
    hits = LH.scan(text)
    if hits:
        return False, "%d 处营销推广痕迹（%s）⇒ scripts/link_hygiene.py --strip 修" % (
            len(hits), hits[0][0])
    return True, "干净"


def _gate_katex(platform, case, pack, **kw):
    if not pack or not pack.lower().endswith(".md"):
        return True, "非 md，跳过"
    hits = KX.scan_file(pack)
    if hits:
        return False, "%d 处 `$N` 会被吃成公式（例 %s）⇒ scripts/katex_money_fix.py %s --go" % (
            len(hits), hits[0], os.path.relpath(pack, ROOT))
    return True, "无 `$N`"


def _gate_tt(platform, case, pack, **kw):
    text, is_html = _html_of(pack)
    fs = TT.scan(text, is_html=is_html)
    level = TT.worst(fs)
    if level == "block":
        bad = [f for f in fs if f.level == "block"]
        return False, "%d 条 block（%s）⇒ scripts/tt_compliance.py --fix" % (
            len(bad), bad[0].label)
    if level == "warn":
        return True, "warn %d 条（不拦，但别当没看见）" % len([f for f in fs if f.level == "warn"])
    return True, "干净"


def _gate_genre(platform, case, pack, **kw):
    text, _ = _html_of(pack)
    head = ""
    for line in text.splitlines():
        if line.startswith("# "):
            head = line[2:].strip()
            break
    if not head:
        head = case
    hit = [w for w in MONEY_WORDS if w in head]
    if hit:
        return False, "标题含「%s」⇒ 腾讯云只收技术向（商务体两篇全被驳）" % hit[0]
    return True, "技术向标题：%s" % head[:24]


def _gate_cover(platform, case, pack, **kw):
    """封面：本地有资源 + 对 `--file` 平台确认发布命令真的带 `--cover`。

    只查本地 PNG 是不够的：2026-11-04 CSDN 三篇草稿「本地有图、平台侧
    coverImage 全空」，因为封面没随 `saveArticle` 一次提交。
    """
    c = G.check_cover(platform, case)
    if not c["ok"]:
        return False, c["detail"]
    if platform in G.COVER_ARG_PLATFORMS:
        cmd = build_cmd(platform, case, pack, True)
        if "--cover" not in cmd:
            return False, "发布命令没带 --cover ⇒ 本地有图也不会落到平台侧"
        return True, "%s ｜ 命令已带 --cover" % c["detail"]
    return True, c["detail"]


def _gate_cover_remote(platform, case, pack, **kw):
    """封面回读：平台侧到底有没有封面（`scripts/cover_verify.py`）。

    `_gate_cover` 是**静态**的（本地有没有图、命令带不带 --cover），
    这一道是**动态**的（平台侧字段读出来是什么）。
    只有「真读了、读出来是空的」才拦；「没查过」只提示 ——
    否则闸会变成「没记录 = 通过」的假绿发生器。
    """
    try:
        import cover_verify as CV
    except Exception as e:                                 # noqa: BLE001
        return True, "⚠ 回读模块不可用（%s），按未回读处理" % str(e)[:60]
    v = CV.verify(platform, case)
    if v["state"] == "ok":
        return True, "平台侧已落库：%s" % v["evidence"][:70]
    if v["state"] == "empty":
        return False, ("%s ⇒ 补封面后再发，别把裸图发出去"
                       % v["detail"][:150])
    return True, "⚠ %s" % v["detail"][:150]


def _gate_dedupe(platform, case, pack, force=False, **kw):
    """case 级幂等：已发过 / 已有远端草稿都不许再来一次。

    `--force` 只绕这一道闸（补发确有其事），且必须显式给；标题/形态/封面
    三道不许绕 —— 那是内容质量，不是「要不要重发」的选择题。
    """
    r = G.check_dupe(platform, case)
    if r["ok"]:
        return True, r["detail"]
    if force:
        return True, "⚠ 显式 --force 放行（%s）" % r["detail"]
    return False, r["detail"]


def _gate_title(platform, case, pack, **kw):
    """标题：平台上限 / 硬截断 / 空壳模板 / AI 套话 / 金额与 `$`。"""
    t = G.title_of(pack)
    if not t and pack and os.path.isdir(pack):
        # 头条/小红书/B站的二版产物是目录，标题由各自脚本现算，
        # 静态取不到就别误拦 —— 但复核提示要留着。
        return True, "产物目录无静态标题 ⇒ 发前跑 audit_cover_title.py 复核"
    fs = G.scan_title(platform, t, pack)
    bad = [f for f in fs if f["level"] == "block"]
    if bad:
        return False, "%d 条（%s：%s）" % (len(bad), bad[0]["rule"], bad[0]["detail"])
    if fs:
        return True, "%d 字 / 上限 %d，warn %d 条（%s）" % (
            len(t), G.TITLE_MAX.get(platform, 100), len(fs), fs[0]["detail"][:60])
    return True, "%d 字 / 上限 %d，干净" % (len(t), G.TITLE_MAX.get(platform, 100))


def _gate_template(platform, case, pack, **kw):
    """内容形态：管线水印 / 罐头来源句 / 机械栏目 / 知乎导流。"""
    fs = G.scan_content(platform, None, pack)
    bad = [f for f in fs if f["level"] == "block"]
    if bad:
        return False, "%d 条（%s：%s）⇒ 改稿或重建产物（不要 --force）" % (
            len(bad), bad[0]["rule"], bad[0]["detail"])
    if fs:
        return True, "warn %d 条（%s）" % (len(fs), fs[0]["detail"][:60])
    return True, "形态干净"


def _gate_rules(platform, case, pack, **kw):
    """CSDN《社区内容创作规范》硬闸（scripts/csdn_rules.py）。"""
    if not pack or not pack.lower().endswith(".md"):
        return True, "非 md，跳过"
    R = CR
    title, body = R.read_md(pack)
    v = R.scan(title, R.clean(body))
    if v["block"]:
        it = v["block"][0]
        return False, "%d 条会驳回（%s：%s）⇒ scripts/csdn_rules.py %s" % (
            len(v["block"]), it["rule"], it["spec"], os.path.relpath(pack, ROOT))
    return True, "无违规词条（warn %d 条）" % len(v["warn"])


def _yun_row(case, platform):
    """从 SQLite ledger 读两朵云发布记录，返回兼容 yun_finalize.json 格式的 dict。

    映射：draft_id → tc_draft/al_draft，remote_id → tc_article/al_article，
    state → tc_state/al_state，url → tc_url/al_url。
    找不到返回 {}。
    """
    conn = db.get_conn()
    try:
        p = db.get_post(conn, case, platform)
        if not p:
            return {}
        key = "tc_" if platform == "tencentcloud" else "al_"
        row = {}
        if p.get("draft_id"):
            row[key + "draft"] = p["draft_id"]
        if p.get("remote_id"):
            row[key + "article"] = p["remote_id"]
        if p.get("state"):
            row[key + "state"] = p["state"]
        if p.get("url"):
            row[key + "url"] = p["url"]
        if p.get("title"):
            row[key + "title"] = p["title"]
        if p.get("note"):
            row[key + "note"] = p["note"]
        if p.get("updated_at"):
            row["updated"] = p["updated_at"]
        return row
    finally:
        db.close_conn()


def _gate_remote(platform, case, pack, **kw):
    row = _yun_row(case, platform) or {}
    key = "tc_draft" if platform == "tencentcloud" else "al_draft"
    did = str(row.get(key) or "")
    if not did:
        return False, "台账没有远端草稿 id（还没上传到 %s 草稿箱）" % platform
    state = row.get("tc_state" if platform == "tencentcloud" else "al_state") or ""
    # 🚨 2026-11-04 修：旧写法恒读 `tc_state`，阿里云分支永远拿不到状态
    #   ⇒ 已 published 的稿子也过闸，重复发不会被拦。
    if state == "published":
        art = row.get("tc_article" if platform == "tencentcloud" else "al_article") or "?"
        return False, "台账已是 published（article %s）⇒ 重复发会出第二篇" % art
    return True, "draftId=%s" % did


GATE_FN = {
    "dedupe": _gate_dedupe, "draft": _gate_draft, "link": _gate_link,
    "katex": _gate_katex, "tt": _gate_tt, "genre": _gate_genre,
    "cover": _gate_cover, "rules": _gate_rules,
    "cover_remote": _gate_cover_remote,
    "title": _gate_title, "template": _gate_template,
    "remote": _gate_remote,
}


def run_gates(platform, case, pack, force=False):
    rows = []
    for name in GATES.get(platform, ["dedupe", "draft"]):
        fn = GATE_FN[name]
        try:
            ok, detail = fn(platform, case, pack, force=force)
        except SystemExit as e:                     # 闸自己 raise 也算拦下
            ok, detail = False, str(e).strip().splitlines()[0][:120]
        rows.append({"gate": name, "ok": bool(ok), "detail": detail})
    return rows


# ---------------------------------------------------------------- 命令

def build_cmd(platform, case, pack, yes):
    """真正的发布命令。每个平台的参数口径都来自该脚本自己的 argparse。"""
    s = lambda *a: list(a)
    if platform == "wechat":
        cmd = s("wechat_publish.py", "publish", "--file", pack, "--slug", case)
    elif platform == "toutiao":
        cmd = s("toutiao_publish.py", "publish", "--case", case)
        if yes:
            cmd.append("--yes")
    elif platform == "xhs":
        cmd = s("xhs_publish.py", "publish", "--case", case)
        if yes:
            cmd.append("--yes")
    elif platform == "bilibili":
        cmd = s("bilibili_publish.py", "publish", "--case", case)
    elif platform == "bilibili2":
        # 🚨 二号**不能**走老仓 `bilibili_publish.py`：它按 `--case` 读
        #   `out/bili/<cid>/`（一号的游戏版正文），发出去就是两号同稿 ⇒ 判重复投稿。
        #   二号必须走游戏仓 bridge：按 free 变体取稿、把 h1 换成 title_alt、
        #   stage 成 `out/articles/<cid>-b2.html`，再建草稿 + 发表 + 自己补账。
        #   slug 要剥 `ext-`（游戏仓内容目录是 `content/ep-003-freedoom/`）。
        slug = case[4:] if str(case).startswith("ext-") else case
        cmd = s(os.path.join(SLOT.GAMES_ROOT, "publish", "bridge.py"),
                "publish", "--slug", slug, "--platform", "bilibili2")
    elif platform == "csdn":
        cmd = s("csdn_publish.py", "publish", "--file", pack, "--case", case)
        # 封面必须随发表一起提交（saveArticle 的 cover_images）；
        # 事后补封面要重发一次、重新过审，2026-11-04 三篇全裸图就是漏了这一步
        c = cover_of(case)
        if c:
            cmd += ["--cover", c]
    elif platform == "cnblogs":
        cmd = s("cnblogs_publish.py", "publish", "--file", pack, "--case", case)
    elif platform == "zhihu":
        cmd = s("zhihu_publish.py", "draft", "--file", pack, "--case", case)
    elif platform in ("tencentcloud", "aliyun"):
        row = _yun_row(case, platform) or {}
        key = "tc_draft" if platform == "tencentcloud" else "al_draft"
        cmd = s("yun_finalize.py", "--case", case, "--file", pack,
                "--publish", "--i-said-publish", "i-said-publish",
                "--" + key.replace("_", "-"), str(row.get(key) or ""))
        # ⚠ 阿里云必带 --headed：滑块验证码是人机验证，无头窗口用户看不见
        #   ⇒ 拖不了。2026-11-03 踩过：build_cmd 漏传，yun_finalize 自己
        #   起了无头实例，用户报告「看不到滑块」。
        if platform == "aliyun":
            cmd.append("--headed")
        c = cover_of(case)
        if c:
            cmd += ["--cover", c]
    else:
        raise SystemExit("未知平台：%s" % platform)
    cmd = [os.path.normpath(c) for c in cmd]
    if not yes and platform not in ("wechat", "bilibili", "bilibili2", "csdn",
                                    "cnblogs", "zhihu"):
        cmd.append("--dry")
    return [PY, "-X", "utf8"] + [os.path.join(SCRIPTS, cmd[0])] + cmd[1:]


# ---------------------------------------------------------------- 选稿 / 计划

def pick(platform, case=None, day=None, allow_same_kind=False):
    """返回 (case, 档期行) 或 (None, 档期行)。"""
    row = SLOT.decide(platform, day, allow_same_kind=allow_same_kind)
    pool = t = row.get("candidates") or []
    if case:
        if case in t:
            return case, row
        # 允许显式指定池外的稿（补发/重发），但要说清为什么不在池里
        row = dict(row, note="指定 %s 不在今日候选池（%s）" % (
            case, "、".join(t[:3]) or "空"))
        return case, row
    return (t[0] if t else None), row


def plan(day=None, platforms=None, as_json=False, allow_same_kind=False):
    rows = []
    for key, cn in SLOT.PLATFORMS:
        if platforms and key not in platforms:
            continue
        d = SLOT.decide(key, day, allow_same_kind=allow_same_kind)
        case, _ = pick(key, day=day, allow_same_kind=allow_same_kind)
        pack = pack_path(key, case) if case else None
        gates = run_gates(key, case, pack) if case else []
        rows.append({
            "platform": key, "name": cn, "kind": d.get("kind"),
            "case": case, "pack": pack, "terminal": TERMINAL[key],
            "reason": d.get("reason"), "blocked": d.get("blocked"),
            "note": d.get("note"), "gates": gates,
            "ready": bool(case) and all(g["ok"] for g in gates),
        })
    if as_json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return 0

    print("今日发布计划 %s ｜ 规矩：两类交替（配额 1 的平台按天轮流；配额 ≥2 的先补没发过的类，"
          "两类都发过后接着交替）\n" % SLOT.today(day))
    print("%-12s %-8s %-22s %-6s %s" % ("平台", "该发", "稿件", "闸", "说明"))
    print("-" * 108)
    for r in rows:
        if not r["kind"]:
            print("%-12s %-8s %-22s %-6s %s" % (r["name"], "不发", "—", "—", r["reason"] or ""))
            continue
        bad = [g for g in r["gates"] if not g["ok"]]
        if not r["case"]:
            st = "✗ 无稿"
        else:
            st = "✓ 可发" if r["ready"] else "✗ %d 条拦" % len(bad)
        print("%-12s %-8s %-22s %-6s %s" % (
            r["name"], SLOT.KIND_LABEL[r["kind"]], r["case"] or "无稿", st,
            "终点=%s" % ("草稿" if r["terminal"] == "draft" else "发表")))
        for g in bad:
            print("             └ [%s] %s" % (g["gate"], g["detail"]))
        if r["note"]:
            print("             └ %s" % r["note"])
    print("\n下一步：python scripts/publish_one.py --platform <平台> --yes")
    return 0


# ---------------------------------------------------------------- 执行

def upload_remote(platform, case, dry=False):
    """把稿子上传到两朵云草稿箱（拿到 tc_draft / al_draft）。

    ⚠ 2026-11-04 修：显式带 `--via cdp`（`yun_draft_cdp.py`，9228 常驻
    Chrome，登录态磁盘持久），**不依赖 CSDN 同步助手扩展**。旧写法漏传
    `--via`，而 `sync_longtail.py` 默认是 `mcp` ⇒ 扩展没连上时 `--upload`
    必失败，报错还把人往「浏览器/扩展没开」上带，其实两朵云早有
    不依赖扩展的通道（sync_longtail 的 help 自己都写着「推荐」）。
    仍不做默认自动执行 —— 失败时给明确的下一步，不静默跳过。
    """
    cmd = [PY, "-X", "utf8", os.path.join(SCRIPTS, "sync_longtail.py"),
           "--case", case, "--platforms", platform, "--via", "cdp"]
    print("上传草稿：%s" % " ".join(_q(c) for c in cmd))
    if dry:
        return 0
    return subprocess.call(cmd, cwd=ROOT)


# 草稿箱即 FIFO 队列的平台（用户定，2026-11-03）：发最久草稿，箱空才新写。
# 两家的 send-draft 都「默认取存入最久的一条」，正好是队列语义。
# 🚨 **二号 2026-11-06 晚进表**：此前不进的理由是「箱里积着 10-31 批量建的 8 条
#   旧稿（旧栏目标题 + 旧口径正文），走 FIFO 就先把旧稿发出去」。那 8 条已全部
#   换技术向标题、正文对齐母版、删掉栏目标语（PRD #69/#73/#75）⇒ 理由消失。
#   现在走 FIFO 省掉每天的 bridge 建稿/复用，直接发箱里最久一条。
#   ⚠ 代价：FIFO **不经过 bridge**，母版以后再改（如再动正文）不会同步到箱里
#   旧稿 —— 要同步得先跑 `bili2_draft_refresh.py` 再发。
#   ⚠ 二号是**另一个账号**：端口 9224 + chrome-bili2-profile，靠环境变量切换，
#   见 `BILI2_ENV`。漏传就会在一号的浏览器里找二号草稿箱（症状像「箱空」）。
# 🚨 2026-11-07 补上公众号：11-03 定「草稿箱是 FIFO」时只接了 B站/小红书，
#    wechat 仍是 `publish --file` 新写 ⇒ 11-07 又往箱里塞了第 12 条，且与
#    10-31 已发表的《游戏厅：Freedoom》同标题重复（`ep-*` / `ext-ep-*` 两套
#    id 让 dedupe 没拦住）。公众号走 `wechat_fifo.py`（远端对账 + 剔已发表
#    同名 + 按 update_time 挑最久），不是 send-draft，见 FIFO 分支与脚本模块头。
FIFO_PLATFORMS = ("bilibili", "xhs", "bilibili2", "wechat")

# 二号账号的进程环境（与 `cover_verify` 的能力表、`_launch_cdp.py --name bili2` 同值）。
# `bilibili_publish.PUB_PORT` 读 `CDP_BILI_PORT`，profile 读 `CDP_PLAT_PROFILE`。
BILI2_ENV = {
    "CDP_BILI_PORT": "9224",
    "CDP_PLAT_PROFILE": os.path.expandvars(r"%USERPROFILE%\chrome-bili2-profile"),
}


def _fifo_env(platform):
    """FIFO 子进程要带的环境：二号切账号，其余平台沿用当前环境。"""
    if platform == "bilibili2":
        return dict(os.environ, **BILI2_ENV)
    return None


def fifo_note(platform):
    """send-draft 发的那篇不经过本地选稿 ⇒ 发完把它的类型补进档期日志。

    档期轮换（AI/游戏按天轮流）只管「新写」时的选稿；FIFO 发出的旧稿类型
    可能与当日档期不同，如实按台账记，别让轮换计数失真。

    二号直接读 `SLOT.records('bilibili2')`（游戏仓 ∪ 本仓 db 的并集，见
    `publish_slot._rows_games_bili2`）—— 它发完只写本仓 db，
    本仓 `publish_manifest.json` 里没有它的记录，按 manifest 找永远是空。
    """
    try:
        cid = None
        if platform == "bilibili2":
            today = SLOT.today()
            hit = [r for r in SLOT.records("bilibili2") if r[0].startswith(today)]
            cid = hit[-1][1] if hit else None
        elif platform == "bilibili":
            m = _load("publish_manifest.json") or {}
            best = ""
            for c, plats in m.items():
                b = (plats or {}).get("bilibili") if isinstance(plats, dict) else None
                if isinstance(b, dict) and b.get("state") == "published" \
                        and (b.get("at") or "")[:10] == SLOT.today():
                    if (b.get("at") or "") > best:
                        best, cid = b.get("at"), c
        elif platform == "wechat":
            # send 自己写两本台账；这里按「今天 + sent_via=send」反查刚发的那条。
            # 不补就会漏记档期（明天按上次类型又轮到同一类）。
            today = SLOT.today()
            best = ""
            for path in ("wechat_published.json", "wechat_standalone.json"):
                for c, r in (_load(path) or {}).items():
                    r = r or {}
                    at = str(r.get("published_at") or "")
                    if (r.get("status") == "published" and at.startswith(today)
                            and r.get("sent_via") == "send" and at > best):
                        best, cid = at, c
        else:  # xhs：台账是 [{id, at}, …]，取今天最新一条
            rows = _load("xhs_published.json") or []
            today = SLOT.today()
            hit = [r for r in rows if str(r.get("at") or "").startswith(today)]
            if hit:
                cid = hit[-1].get("id")
        if not cid:
            return
        path = os.path.join(DATA, "publish_kind_log.json")
        d = atomicio.read_json(path, default={})
        rec = d.setdefault("records", {}).setdefault(platform, [])
        if any(r.get("case_id") == cid for r in rec):
            return
        rec.append({"at": SLOT.today() + " " + time.strftime("%H:%M"),
                    "case_id": cid, "kind": SLOT.kind_of(cid),
                    "note": "send-draft FIFO 最久草稿"})
        with atomicio.file_lock(path + ".lock", timeout=30.0):
            atomicio.write_json(path, d, indent=1)
        print("  台账：publish_kind_log.json 记 %s/%s（%s）"
              % (platform, cid, SLOT.kind_of(cid)))
    except Exception as e:                                  # noqa: BLE001
        print("  ⚠ 档期日志补记失败（不影响发布）：%s" % str(e)[:80])


def gen_pack(platform, case):
    """按需生成一篇 AI case 平台稿（gen_case_drafts.py）；失败返回 None。

    ⚠ 必须带 NO_PROXY：ModelScope 端点走直连（本机有全局代理时会 403/超时，
    与 scripts 直连发布同一坑）。生成失败不拦发布 —— 它只负责把稿补出来，
    补不出来就走原来的「稿件（缺）」路径报闸。
    """
    env = dict(os.environ, NO_PROXY="*", no_proxy="*")
    cmd = [PY, "-X", "utf8", GEN_SCRIPT, "--case", case, "--platforms", platform]
    try:
        rc = subprocess.call(cmd, cwd=ROOT, env=env)
    except OSError as e:
        print("  ✗ 生成失败：%s" % e)
        return None
    return pack_path(platform, case) if rc == 0 else None


def run(platform, case=None, yes=False, timeout=900, day=None, upload=False,
        as_json=False, gen=False, new=False, force=False,
        allow_same_kind=False):
    cn = dict(SLOT.PLATFORMS)[platform]
    # 草稿箱即 FIFO 队列（用户定，2026-11-03）：先发**存入最久**的那条，
    # 箱空了才按当日档期新写。旧 SPEC 接成 `publish`（无脑新写）是错的——
    # B站一天只烧 1 篇额度，新写等于把箱里积压的稿晾着、额度拿去发新稿；
    # 小红书同病：箱里 48 条还往里塞第 49 条。send-draft 本来就「默认取
    # 存入最久的一条」，这里只是把入口接对。
    if platform in FIFO_PLATFORMS and not new and not case:
        # 🚨 2026-11-04：FIFO 分支原本连档期都不看，直接 send-draft。
        #   「今日已到上限」时重跑一次就是第二篇 ⇒ 先把配额闸补上。
        d = SLOT.decide(platform, day, allow_same_kind=allow_same_kind)
        if not d.get("kind"):
            print("✗ %s 今天不发/已到上限：%s" % (cn, d.get("blocked") or d.get("reason")))
            print("  （FIFO 发的是箱内存最久的一条；重跑前先按标题查已发表列表，别重发）")
            return 1
        print("▶ %s ｜ FIFO：发草稿箱里存入最久的一条（send-draft 默认）" % cn)
        print("  规则：箱空才新写 —— 那时加 --new 走当日档期选稿。")
        print("  ⚠ 这条路径不经过本地选稿；失败先查已发表列表，别原地重跑。")
        if as_json:
            print(json.dumps({"platform": platform, "mode": "fifo"},
                             ensure_ascii=False))
            return 0
        if platform == "wechat":
            # 公众号的「最久」必须按**远端对账**算：本地台账滞后，且草稿箱里
            # 混着已发表过的同名旧稿。干跑也做一次只读对账，让人先看到今天
            # 要发箱里哪一条；真发加 --go，最后一步仍需管理员扫码。
            cmd = [PY, "-X", "utf8", os.path.join(SCRIPTS, "wechat_fifo.py"),
                   "--list"] + (["--go"] if yes else [])
            print("  ⚠ 终点=发表（发布模式不推送、不占额度）；扫码框出现后需管理员微信扫")
            rc = subprocess.call(cmd, cwd=ROOT, env=_fifo_env(platform))
            if rc == 0 and yes:
                fifo_note(platform)
            return rc
        script = ("xhs_publish.py" if platform == "xhs"
                  else "bilibili_publish.py")
        if platform == "bilibili2":
            # 干跑也要说：二号和一号是两个浏览器，看错账号会以为「箱空」
            print("  账号：二号 9224 / chrome-bili2-profile（不是一号）")
        if not yes:
            print("\n（干跑）将会执行：\n  %s" % _q(os.path.join(
                SCRIPTS, script)) + " send-draft")
            return 0
        print("\n执行 send-draft…")
        rc = subprocess.call([PY, "-X", "utf8",
                              os.path.join(SCRIPTS, script), "send-draft"],
                             cwd=ROOT, env=_fifo_env(platform))
        if rc != 0:
            print("\n✗ send-draft 未成功。两种常见情形：")
            if platform == "bilibili2":
                print("  · 箱空 → 先按档期建稿：python scripts/publish_one.py "
                      "--platform bilibili2 --new --yes")
                print("  · 反查不到 case（「请用 --case 指定」）→ 箱里那条标题"
                      "与游戏仓 meta.title_alt 不一致，先跑 bili2_draft_refresh.py")
            else:
                print("  · 箱空 → 新写：python scripts/publish_one.py "
                      "--platform %s --new --yes" % platform)
            print("  · 点了发布但回读没命中 → B站常见 toast「提交成功」+ aid "
                  "重分配，按标题查已发表列表补账（别重发）")
        else:
            fifo_note(platform)
        return rc

    case, row = pick(platform, case, day, allow_same_kind=allow_same_kind)
    cn = dict(SLOT.PLATFORMS)[platform]
    if not row.get("kind"):
        print("✗ %s 今天不发：%s" % (cn, row.get("reason")))
        return 1
    if not case:
        if row.get("need_draft"):
            # 2026-11-05 用户定「无稿补写」：轮到 AI 无稿 => 从源补收录最久的一篇，不轮空。
            if gen and platform in GEN_PLATFORMS:
                import gen_case_drafts as _G
                case = _G.pick_by_oldest(platform)
                if not case:
                    print("✗ %s 想补稿但库里挑不出 eligible 的未发 case" % cn)
                    return 1
                print("  无稿补写：从源挑收录最久 => %s（稿件随后按需生成）" % case)
            else:
                tip = ("按无稿补写规则需先产出稿件：四平台加 --gen 自动生成；"
                       "腾讯云只收技术向稿，走人工重写（参照 drafts/newbatch/tencentcloud/mort.md 口径）")
                print("✗ %s 「AI case」无现成稿。%s" % (cn, tip))
                return 1
        else:
            print("✗ %s 今日无稿可发：%s" % (cn, row.get("blocked") or row.get("reason")))
            return 1

    pack = pack_path(platform, case)
    print("▶ %s ｜ %s ｜ %s" % (cn, case, SLOT.KIND_LABEL.get(
        SLOT.kind_of(case), "?")))
    print("  档期：%s" % row.get("reason"))
    if row.get("note"):
        print("  ⚠ %s" % row["note"])
    if not pack and gen and platform in GEN_PLATFORMS:
        # AI 稿库断供（五个 --file 平台共用同 3 篇早发完了）⇒ 按需生成这一篇。
        # ⚠ 档期在 AI 池空时会**回退到游戏稿**（decide 的 fallback），显式 --gen
        # 就是要把 AI 稿补出来 ⇒ 此时选稿交给 gen_case_drafts（按质量挑第一篇
        # 没发过的），别顺着回退把游戏稿发了 —— 那会多烧一期游戏额度。
        # 显式 --gen 才做，不静默：生成的稿子标题是模型写的，人得有机会看到。
        fb = row.get("fallback") or ""
        ai_wanted = row.get("kind") == "ai" or (SLOT.kind_of(case) == "game"
                                                and "改发 AI" in fb)
        if ai_wanted:
            if SLOT.kind_of(case) == "game":
                import gen_case_drafts as _G
                new_case = _G.pick_by_oldest(platform)
                if new_case:
                    case = new_case
                    pack = pack_path(platform, case)
                    print("  档期本该发 AI 但稿库为 0 ⇒ 按质量补选：%s" % case)
            if not pack and SLOT.kind_of(case) == "ai":
                print("  稿件缺货 ⇒ 按需生成（gen_case_drafts，模板拼装+LLM 只写标题）")
                pack = gen_pack(platform, case)
                if pack:
                    print("  稿件：%s（新生成）" % pack)
    print("  稿件：%s" % (pack or "（缺）"))

    gates = run_gates(platform, case, pack, force=force)
    if as_json:
        print(json.dumps({"platform": platform, "case": case, "pack": pack,
                          "gates": gates,
                          "ready": all(g["ok"] for g in gates)}, ensure_ascii=False, indent=1))
        return 0 if all(g["ok"] for g in gates) else 1
    # remote 闸不过 + 用户给了 --upload ⇒ 先补上传，再重跑闸（别的闸不因此放行）
    if upload and platform in ("tencentcloud", "aliyun") and any(
            g["gate"] == "remote" and not g["ok"] for g in gates):
        if upload_remote(platform, case, dry=not yes) == 0:
            gates = run_gates(platform, case, pack, force=force)
        else:
            print("✗ 上传失败（CDP 通道）：查 9228 Chrome 是否常驻、"
                  "chrome-yun-profile 登录态是否还在，按上面报错处理")
            return 1
    for g in gates:
        print("  %s %-6s %s" % ("✓" if g["ok"] else "✗", g["gate"], g["detail"]))
    if not all(g["ok"] for g in gates):
        print("\n✗ 被闸拦下，未执行任何写操作。修完再来。")
        return 1
    if not yes:
        cmd = build_cmd(platform, case, pack, False)
        print("\n（干跑）将会执行：\n  %s" % " ".join(_q(c) for c in cmd))
        print("\n加 --yes 才真发。")
        return 0

    if force:
        print("\n⚠ --force 只绕过 dedupe 闸；title / template / cover / remote 照常硬拦。")
    cmd = build_cmd(platform, case, pack, True)
    print("\n执行：%s\n" % " ".join(_q(c) for c in cmd))
    t0 = time.time()
    rc = subprocess.call(cmd, cwd=ROOT)
    dt = time.time() - t0
    print("\n%s 退出码 %d，用时 %.0fs" % (cn, rc, dt))
    if rc == 0:
        print("复核：%s" % VERIFY_HINT.get(platform, "—"))
        _note_kind(platform, case)
    else:
        print("失败先别重跑：%s" % VERIFY_HINT.get(platform, "—"))
        print("（「发布成功」唯一判据是远端列表按标题命中；脚本报错也可能其实成功了）")
    return 0 if rc == 0 else 1


def _note_kind(platform, case):
    """补记档期日志：头条的 ext-ep-* 不进 toutiao_published.json，只能这里补。

    ⚠ 别的平台**台账自己就读得到**，再补一条会让 `records()` 把同一篇算两次
    —— 2026-11-03 实测：博客园发完后补记，quota=2 被一条稿占满，当天第二篇
    直接被判「今日已发 2 篇」。所以先按归一口径查一遍，台账里已经有就不补。
    """
    try:
        sent = {SLOT._norm_id(cid) for _, cid in SLOT.records(platform)}
    except Exception:                                      # noqa: BLE001
        sent = set()
    if SLOT._norm_id(case) in sent:
        return
    path = os.path.join(DATA, "publish_kind_log.json")
    data = _load("publish_kind_log.json") or {}
    rec = data.setdefault("records", {})
    rows = rec.setdefault(platform, [])
    stamp = time.strftime("%Y-%m-%d %H:%M")
    if any(r.get("case_id") == case and str(r.get("at", ""))[:10] == stamp[:10]
           for r in rows):
        return
    rows.append({"case_id": case, "kind": SLOT.kind_of(case), "at": stamp})
    try:
        with atomicio.file_lock(path + ".lock", timeout=30.0):
            atomicio.write_json(path, data, indent=1)
        print("台账：publish_kind_log.json 记 %s/%s" % (platform, case))
    except (OSError, TimeoutError) as e:                   # noqa: BLE001
        print("⚠ 档期日志没写进去：%s" % e)


def _q(s):
    return '"%s"' % s if " " in str(s) else str(s)


# ---------------------------------------------------------------- CLI

def main(argv=None):
    ap = argparse.ArgumentParser(
        description="一条命令走完发布：选平台→选稿→过闸→发布→回读→台账")
    ap.add_argument("--platform", action="append", choices=[k for k, _ in SLOT.PLATFORMS],
                    help="要跑的平台（可多次；缺省只出计划）")
    ap.add_argument("--case", help="指定案例 id（缺省取今日档期候选的第一篇）")
    ap.add_argument("--all", action="store_true", help="今天该发的平台全跑（串行）")
    ap.add_argument("--yes", action="store_true", help="真发（缺省干跑）")
    ap.add_argument("--json", action="store_true", help="计划表机器可读")
    ap.add_argument("--day", help="预演某天（YYYY-MM-DD）")
    ap.add_argument("--timeout", type=int, default=900, help="单平台超时秒数")
    ap.add_argument("--upload", action="store_true",
                    help="两朵云没草稿时先跑 sync_longtail 上传（要浏览器+扩展）")
    ap.add_argument("--gen", action="store_true",
                    help="AI 稿缺货时按需生成（gen_case_drafts，模板拼装+LLM 只写标题）")
    ap.add_argument("--new", action="store_true",
                    help="B站/小红书跳过 FIFO 直发，按当日档期新写草稿（箱空时用）")
    ap.add_argument("--allow-same-kind", action="store_true",
                    help="轮到的类无稿时显式回退另一类（连发同类破坏交替，默认拦）")
    ap.add_argument("--force", action="store_true",
                    help="补发时只绕过 dedupe 闸（标题/形态/封面/remote 仍硬拦）；"
                         "默认不许对已发过或已有草稿的 case 再来一次")
    a = ap.parse_args(argv)

    if a.all and a.platform:
        ap.error("--all 与 --platform 只能二选一")
    if a.all:
        todo = [k for k, _ in SLOT.PLATFORMS
                if SLOT.decide(k, a.day, allow_same_kind=a.allow_same_kind).get("kind") and (a.yes or True)]
        if not a.yes:
            return plan(a.day, todo, a.json, allow_same_kind=a.allow_same_kind)
        rc = 0
        for k in todo:
            rc |= run(k, None, True, a.timeout, a.day, a.upload, a.json, a.gen,
                      a.new, a.force, a.allow_same_kind)
        return rc
    if not a.platform:
        return plan(a.day, None, a.json, allow_same_kind=a.allow_same_kind)
    if len(a.platform) > 1 and a.case:
        ap.error("--case 只能配单个 --platform")
    rc = 0
    for k in a.platform:
        rc |= run(k, a.case, a.yes, a.timeout, a.day, a.upload, a.json, a.gen,
                  a.new, a.force, a.allow_same_kind)
    return rc


if __name__ == "__main__":
    sys.exit(main())
