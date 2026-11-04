# -*- coding: utf-8 -*-
"""清掉公众号产物 `<h1>` 里的数据层口径旁注（2026-11-04，用户批准方案 ①）。

背景：`promote_to_candidates.py` 生成的 `metrics.headline` 带
「，TrustMRR 实时核对（非订阅制，MRR 恒 0）」这类**给编辑看的旁注**，
旧版 `make_article.build_titles()` 把它整段拼进 `<h1>` ⇒ 188 篇产物标题
形如「Name：MRR $835，TrustMRR 实时核对」。生成器根因已由
`make_article._clean_title_hook()` 修好，本脚本只清**存量产物**。

范围（用户批准）：只清 **175 篇未发布**产物的旁注后缀；**13 篇已发布一律不动**，
避免与已发表列表 / 台账里记着的旧标题认亲错位。

保护集合运行时五源求交（不写死 ID，台账变了会自动跟着变）：
  ① `data/wechat_published.json`  `status=published`
  ② `data/publish_manifest.json`  `wechat.state=published`
  ③ `data/ledger.db`  `platform_posts(wechat, state=published)`
  ④ 任一 `data/*.json` 的标题字段与产物 h1 **完全一致**（草稿箱已存过该标题）
  ⑤ `data/wechat_standalone.json` 里提到该 case
实测（2026-11-04）：188 篇带旁注 / 保护 13 / 可清理 175。

⛔ 只改 `<h1>` 内部。正文「官方口径」数据卡里的同一串是事实标注，必须保留。
写盘走 `atomicio.write_text(newline="")`，保持 LF + UTF-8 无 BOM，不碰 `data/`。

用法：
  python scripts/clean_title_caliber.py --check     # 默认，只读
  python scripts/clean_title_caliber.py --apply     # 先备份再落盘
  python scripts/clean_title_caliber.py --verify    # 验收 + 零改动证明
"""
import argparse
import glob
import html
import json
import os
import pathlib
import re
import shutil
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import atomicio                                     # noqa: E402

ARTICLES = os.path.join(ROOT, "out", "articles")
DATA = os.path.join(ROOT, "data")
BACKUP = os.path.join(ROOT, "_backups", "title-caliber-2026-11-04")

H1 = re.compile(r"(<h1\b[^>]*>)(.*?)(</h1>)", re.S | re.I)
# 两种实测形态：「，TrustMRR 实时核对」/「，TrustMRR 实时核对（非订阅制，MRR 恒 0）」
CALIBER = re.compile(
    r"[，,]\s*TrustMRR\s*(?:实时)?核对(?:\s*[（(][^）)]*[）)])?")
TITLE_FIELDS = ("title", "tc_title", "al_title", "wechat_title")


def read(path):
    """保真读：newline='' 不做行尾翻译。"""
    with open(path, "r", encoding="utf-8", newline="") as f:
        return f.read()


def h1_inner(text):
    m = H1.search(text)
    return m.group(2) if m else None


def plain(inner):
    """h1 内文 → 纯文本标题（反转 HTML 实体，长度/文本比对都用它）。"""
    return html.unescape(re.sub(r"<[^>]+>", "", inner or "")).strip()


def clean_h1(inner):
    """只删旁注，主体「Name：MRR $X」原样留着。"""
    return CALIBER.sub("", inner or "").strip().rstrip("，,、：:；; ")


def replace_h1(text):
    """定点替换：只动 `<h1>` 第 2 个捕获组，其它字节一律原样。"""
    m = H1.search(text)
    if not m:
        return text, False
    new_inner = clean_h1(m.group(2))
    if new_inner == m.group(2):
        return text, False
    return text[:m.start(2)] + new_inner + text[m.end(2):], True


def scan(articles_dir=ARTICLES):
    """{cid: {path,text,inner}}，只收 `<h1>` 带口径旁注的产物。"""
    out = {}
    for p in sorted(glob.glob(os.path.join(articles_dir, "*.html"))):
        text = read(p)
        inner = h1_inner(text)
        if inner and "TrustMRR" in inner and "核对" in inner:
            out[os.path.basename(p)[:-5]] = {
                "path": p, "text": text, "inner": inner}
    return out


def _walk_titles(node, acc):
    if isinstance(node, dict):
        for k, v in node.items():
            if k in TITLE_FIELDS and isinstance(v, str):
                acc.add(v.strip())
            _walk_titles(v, acc)
    elif isinstance(node, list):
        for v in node:
            _walk_titles(v, acc)


def protected_ids(titles, data_dir=DATA, db_path=None):
    """五源求交。`titles` = {cid: 解码后的 h1 纯文本}。纯只读。"""
    prot = set()

    wp = atomicio.read_json(os.path.join(
        data_dir, "wechat_published.json"), {}) or {}
    prot |= {k for k, v in wp.items()
             if isinstance(v, dict) and v.get("status") == "published"}

    mf = atomicio.read_json(os.path.join(
        data_dir, "publish_manifest.json"), {}) or {}
    prot |= {k for k, v in mf.items()
             if isinstance(v, dict) and isinstance(v.get("wechat"), dict)
             and v["wechat"].get("state") == "published"}

    db = db_path or os.path.join(data_dir, "ledger.db")
    if os.path.isfile(db):
        try:
            uri = pathlib.Path(db).absolute().as_uri() + "?mode=ro"
            conn = sqlite3.connect(uri, uri=True)
            prot |= {r[0] for r in conn.execute(
                "SELECT case_id FROM platform_posts "
                "WHERE platform='wechat' AND state='published'")}
            conn.close()
        except sqlite3.Error:
            pass                                   # 台账缺失不算「未发布」

    all_titles = set()
    for f in glob.glob(os.path.join(data_dir, "*.json")):
        try:
            _walk_titles(json.loads(read(f)), all_titles)
        except (OSError, ValueError):
            continue
    prot |= {cid for cid, t in titles.items() if t in all_titles}

    sa = atomicio.read_json(os.path.join(
        data_dir, "wechat_standalone.json"), {}) or {}
    blob = json.dumps(sa, ensure_ascii=False)
    prot |= {cid for cid in titles if cid in blob}

    return prot & set(titles)


def plan(articles_dir=ARTICLES, data_dir=DATA, db_path=None):
    items = scan(articles_dir)
    titles = {cid: plain(v["inner"]) for cid, v in items.items()}
    prot = protected_ids(titles, data_dir, db_path)
    todo = {cid: v for cid, v in items.items() if cid not in prot}
    return items, titles, prot, todo


def backup_name(cid):
    return "out__articles__%s.html" % cid


def cmd_check(args):
    items, titles, prot, todo = plan()
    print("带旁注产物 %d 篇 ｜ 已发布保护 %d 篇 ｜ 可清理 %d 篇"
          % (len(items), len(prot), len(todo)))
    print("保护清单：%s" % ", ".join(sorted(prot)))
    print("\n预览（前 8 条）：")
    for cid in sorted(todo)[:8]:
        old = titles[cid]
        print("  %-42s %2d→%2d ｜ %s"
              % (cid[:42], len(old), len(clean_h1(items[cid]["inner"])),
                 plain(clean_h1(items[cid]["inner"]))))
    if not todo:
        print("\n✅ 幂等：没有待清理文件（已清过，或目录里没有带旁注产物）")
    else:
        print("\n干跑，未写盘。落盘加 --apply（会先备份）。")
    return 0


def cmd_apply(args):
    items, titles, prot, todo = plan()
    if not todo:
        print("没有待清理文件，无需落盘。")
        return 0
    os.makedirs(BACKUP, exist_ok=True)
    # ① 原件备份（只备要改的 175 篇）
    n = 0
    for cid, v in todo.items():
        dst = os.path.join(BACKUP, backup_name(cid))
        if not os.path.isfile(dst):
            shutil.copy2(v["path"], dst)
            n += 1
    # ② baseline 存全部 188 篇的 h1 内文，verify 用它做「除 h1 外零改动」证明
    baseline = {cid: v["inner"] for cid, v in items.items()}
    atomicio.write_text(
        os.path.join(BACKUP, "baseline.json"),
        json.dumps(baseline, ensure_ascii=False, indent=1, sort_keys=True),
        newline="")
    print("已备份 %d 份原件 → %s"
          % (n, os.path.relpath(BACKUP, ROOT)))

    # ③ 定点替换：只动 h1 的第 2 个捕获组
    changed = 0
    for cid, v in todo.items():
        new_text, ok = replace_h1(v["text"])
        if ok:
            atomicio.write_text(v["path"], new_text, newline="")
            changed += 1
    print("已清理 %d 篇 `<h1>` 旁注；%d 篇已发布产物未动。"
          % (changed, len(prot)))
    return 0


def cmd_verify(args):
    bp = os.path.join(BACKUP, "baseline.json")
    baseline = atomicio.read_json(bp, None)
    if not baseline:
        print("✗ 缺 baseline.json，请先跑 --apply")
        return 2
    titles = {cid: plain(inner) for cid, inner in baseline.items()}
    prot = protected_ids(titles)
    todo = set(baseline) - prot
    errs = []

    # ① 已清理的 175 篇：h1 等于期望值、无残留旁注、除 h1 外零字节改动
    for cid in sorted(todo):
        p = os.path.join(ARTICLES, cid + ".html")
        if not os.path.isfile(p):
            errs.append("%s 产物不见了" % cid)
            continue
        cur = read(p)
        inner = h1_inner(cur)
        exp = clean_h1(baseline[cid])
        if inner != exp:
            errs.append("%s h1 不是期望值：%r ≠ %r" % (cid, inner, exp))
        if CALIBER.search(inner or ""):
            errs.append("%s 仍有旁注" % cid)
        bak = os.path.join(BACKUP, backup_name(cid))
        if not os.path.isfile(bak):
            errs.append("%s 缺备份" % cid)
        else:
            orig = read(bak)
            rebuilt, _ = replace_h1(orig)
            if rebuilt != cur:
                errs.append("%s 除 h1 外还有改动" % cid)
            delta = orig.count("TrustMRR 实时核对") - cur.count("TrustMRR 实时核对")
            if delta != 1:
                errs.append("%s 正文数据卡被动过（旁注减少 %d 处，应为 1）"
                            % (cid, delta))

    # ② 13 篇保护对象：h1 与 baseline 逐字一致
    for cid in sorted(prot):
        p = os.path.join(ARTICLES, cid + ".html")
        if not os.path.isfile(p):
            errs.append("%s 保护对象不见了" % cid)
            continue
        if h1_inner(read(p)) != baseline[cid]:
            errs.append("%s 已发布产物被改动" % cid)

    # ③ 全仓标题体检（只读守卫）
    try:
        import publish_guard as G
        blocks, warns = 0, {}
        for p in sorted(glob.glob(os.path.join(ARTICLES, "*.html"))):
            t = G.title_of(p)
            if not t:
                continue
            for f in G.scan_title("wechat", t):
                if f["level"] == "block":
                    blocks += 1
                else:
                    warns[f["rule"]] = warns.get(f["rule"], 0) + 1
        print("全仓公众号标题守卫：block %d ｜ warn %s" % (blocks, warns))
    except Exception as e:                       # 守卫不在时不影响主验收
        print("（守卫统计跳过：%s）" % e)

    print("\n验收：baseline %d 篇 ｜ 保护 %d ｜ 已清理 %d ｜ 错误 %d"
          % (len(baseline), len(prot), len(todo), len(errs)))
    for e in errs[:20]:
        print("  ✗ " + e)
    if not errs:
        print("✅ 除 `<h1>` 外零字节改动；13 篇已发布产物逐字未动；"
              "正文数据卡旁注保留。")
    return 1 if errs else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="清公众号产物 h1 口径旁注（只读默认）")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--check", action="store_true", help="干跑列出计划（默认）")
    g.add_argument("--apply", action="store_true", help="备份后落盘")
    g.add_argument("--verify", action="store_true", help="验收与零改动证明")
    a = ap.parse_args(argv)
    if a.apply:
        return cmd_apply(a)
    if a.verify:
        return cmd_verify(a)
    return cmd_check(a)


if __name__ == "__main__":
    sys.exit(main())
