# -*- coding: utf-8 -*-
"""从小红书草稿箱读回每条草稿的**真实保存时间**，回填 `xhs_drafts.json`
的 `at` 字段。

为什么必须这么干（2026-10-15）：
- `_mark()` 旧实现把 58 条的 `at` 全洗成 `2026-10-15 11:11`，git 只能
  捞回「首次落台账时间」，**捞不出最近一次实际发稿时间**（尤其那 6 个
  「删旧发新」的封面案例）。唯一权威来源是草稿箱卡片上的「保存于 …」。
- ⚠ **只读**：本脚本只 fetch + print + 可选 --apply 写台账，绝不碰草稿箱。

`at` 的语义（写回后）：
- 草稿箱里能查到 ⇒ 草稿箱的「保存于」时间（= 最近一次实际发稿）
- 草稿箱里查不到（已发正式帖 / 已删）⇒ 保留 git 捞回的首次落台账时间，
  并在 `at_note` 字段标明来源，绝不假装是发稿时间。

用法：
  python3 scripts/xhs_read_saved.py            # dry-run，只打印
  python3 scripts/xhs_read_saved.py --apply    # 回填台账
"""
import json
import os
import sys
import time
import importlib.util

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER = os.path.join(ROOT, "data", "xhs_drafts.json")
PROFILE = r"C:\Users\DELL\chrome-debug-profile"
PORT = 9223


def _load_xhs():
    spec = importlib.util.spec_from_file_location(
        "xhs_publish", os.path.join(ROOT, "scripts", "xhs_publish.py"))
    x = importlib.util.module_from_spec(spec)
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    spec.loader.exec_module(x)
    return x


def _norm(s):
    import re
    return re.sub(r"\s+", "", s or "")


def main():
    apply = "--apply" in sys.argv
    x = _load_xhs()

    # 1) 读 git 捞回的首次落台账时间（作为 fallback + 区分依据）
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    rec_spec = importlib.util.spec_from_file_location(
        "xhs_recover_at", os.path.join(ROOT, "scripts", "xhs_recover_at.py"))
    rec = importlib.util.module_from_spec(rec_spec)
    rec_spec.loader.exec_module(rec)
    _, _fixed, _unknown, _n = rec.recover()
    cur0 = json.load(open(LEDGER, encoding="utf-8"))
    git_at = {e.get("id"): e.get("at") for e in cur0}

    mpath = os.path.join(ROOT, "data", "publish_manifest.json")
    manifest = (json.load(open(mpath, encoding="utf-8"))
                if os.path.isfile(mpath) else {})

    # 2) 起 Chrome + 拉草稿箱（只读）
    #    ⚠ 起 Chrome 的函数在 wechat_publish 里（同名被三平台共用），
    #    profile 必须用 chrome_cdp_launch.PLAT_PROFILE —— 那是小红书
    #    登录态所在的 profile，用错 profile 会读到未登录的草稿箱。
    import chrome_cdp_launch as cl
    import wechat_publish as wp
    wp._autostart_chrome_if_needed(PORT, profile=cl.PLAT_PROFILE)
    time.sleep(2)
    drafts = x.fetch_drafts()
    print("草稿箱拉到 %d 条图文草稿\n" % len(drafts))

    # 3) 草稿标题 → case id（复用 _card_to_cid 的 out/xhs/*/note.json 反查）
    #    ⚠ 草稿标题是「钩子：产品名」，note.json 里的正是同一个标题 ⇒ 能精确反查
    title2cid = {}
    base = os.path.join(ROOT, "out", "xhs")
    if os.path.isdir(base):
        for d in sorted(os.listdir(base)):
            f = os.path.join(base, d, "note.json")
            if not os.path.isfile(f):
                continue
            try:
                j = json.load(open(f, encoding="utf-8"))
            except Exception:
                continue
            t = _norm(j.get("title"))
            if t:
                title2cid[t] = d

    box = {}          # cid -> (saved, 标题)
    unmatched = []
    for dr in drafts:
        t = dr.get("title", "")
        nt = _norm(t)
        cid = None
        if nt in title2cid:
            cid = title2cid[nt]
        else:
            # 退化：包含匹配
            for k, v in title2cid.items():
                if k and (k in nt or nt in k):
                    cid = v
                    break
        if not cid:
            unmatched.append(t)
            continue
        saved = dr.get("saved", "").strip()
        prev = box.get(cid)
        # 同一 case 多张稿 ⇒ 取**最近**的那张
        if not prev or saved > prev[0]:
            box[cid] = (saved, t)

    print("=== 反查成功 %d 条（台账 %d 条）===" % (len(box), len(cur0)))
    hit = [c for c in box if c in git_at]
    miss = [c for c in git_at if c not in box]
    print("台账里有、草稿箱也在：%d 条" % len(hit))
    print("台账有、草稿箱查不到：%d 条" % len(miss))
    if miss:
        # ⚠ 这批**不是**"发稿时间未知"，而是"曾发过、现已不在箱"（10-03
        #   「旧稿清空 + 39 条重发」、10-14「清空壳草稿」等操作删了稿，
        #   台账没同步 unmark）。它们没有"最近一次实际发稿时间"可读，
        #   因为**当前根本没有稿**。⇒ 不写 `at`，只标 `at_source`。
        print("  └─ 语义：草稿箱已无此稿（被清空/删除），不是「发稿时间未知」")
        for c in miss:
            m = manifest.get(c, {}).get("xhs", {})
            print("     %-20s manifest.state=%-13s manifest.at=%s"
                  % (c, m.get("state", "(无)"), m.get("at", "-")))
    if unmatched:
        print("草稿箱有、反查不到 case：%d 条 %s" % (len(unmatched), unmatched[:5]))

    print("\n=== 草稿箱保存时间（最近 12 条）===")
    rows = sorted(box.items(), key=lambda kv: kv[1][0], reverse=True)
    for cid, (saved, t) in rows[:12]:
        old = git_at.get(cid, "-")
        flag = "改" if old != saved else "同"
        print("  [%s] %-40s 箱 %s | 台账 %s" % (flag, cid, saved, old))

    changed = [(c, git_at.get(c), box[c][0]) for c in box
               if c in git_at and box[c][0] and git_at.get(c) != box[c][0]]

    # 4) --apply 才写盘
    if not apply:
        print("\n-- 未加 --apply，只读不写。")
        print("   将改写 %d 条 at（草稿箱保存时间 = 最近一次实际发稿）；"
              % len(changed))
        print("   另有 %d 条草稿箱已无稿，at 改用 git 首次落台账时间、"
              "标 at_source='first_mark_git'。" % len(miss))
        return

    entries, nl, was_dict = x._load_ledger(LEDGER)
    table = {c: new for c, _o, new in changed}
    absent = set(miss)
    # git 首次落台账时间表（recover() 会解 10 个 git 快照，别在循环里调）
    git_first = {c: t for c, _o, t in rec.recover()[1]}
    n_upd = n_mark = 0
    for e in entries:
        if e["id"] in table:
            e["at"] = table[e["id"]]
            e["at_source"] = "draft_box"      # = 最近一次实际发稿
            n_upd += 1
        elif e["id"] in absent:
            # ⚠ 这批**没有「最近一次发稿时间」可读** —— 草稿箱里根本没稿
            #   （10-03 清空重发 / 10-14 清空壳时删的，台账没同步 unmark）。
            #   退而用 git 首次落台账时间，但必须**标明来源不同名**，
            #   别拿它冒充发稿时间。
            if e["id"] in git_first:
                e["at"] = git_first[e["id"]]
                e["at_source"] = "first_mark_git"
            n_mark += 1
        elif e.get("at_source") in ("draft_box", "first_mark_git"):
            e.pop("at_source", None)          # 已不在箱，撤掉旧标注
    with open(LEDGER, "w", encoding="utf-8", newline="") as f:
        f.write(json.dumps(entries, ensure_ascii=False, indent=2)
                .replace("\n", nl))
    print("\n✅ 已回填 %d 条 at（at_source=draft_box = 最近一次实际发稿）"
          % n_upd)
    print("   已标注 %d 条 at_source=first_mark_git（草稿箱已无稿，"
          "at = git 首次落台账时间，**不是**发稿时间）" % n_mark)
    print("   行尾 %s" % ("CRLF" if nl == "\r\n" else "LF"))


if __name__ == "__main__":
    main()
