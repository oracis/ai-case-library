"""补回 2026-11-25 被工作区重置洗掉的发布台账（单条 UPDATE/INSERT + manifest 单条合并）。

背景：08:57:26 工作区被重置回 HEAD 并清理未跟踪文件，data/ 下 JSON 与 ledger.db 回到
11-24 基线，今日已发记录全丢。线上发布本身为真（各平台已独立回读确认）。

红线遵守：
- 只对目标 (case_id, platform) 单条 UPDATE / INSERT，不清空任何字段。
- manifest 走 atomicio.update_json 只改目标条目，其余键原样保留（不整本重建）。
- 每条 published_at 均来自平台接口真值，不用 now()。
- 跑完逐条回读打印。

用法：python -u scripts/_oneoff/rebind_today_20261125.py [--apply]
不带 --apply 只打印计划。
"""
import argparse
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import atomicio  # noqa: E402

DB = os.path.join(ROOT, "data", "ledger.db")
MANIFEST = os.path.join(ROOT, "data", "publish_manifest.json")
STAMP = "2026-11-25 09:10:00"

# (case_id, platform, state, remote_id, draft_id, title, url, published_at, note)
FACTS = [
    ("vid-ai", "cnblogs", "published", "23773906", "23773906",
     "用AI视频工作流撑住百万创作者批量出片",
     "https://www.cnblogs.com/thing-explainer/p/23773906",
     "2026-11-25 07:40",
     "11-25 补账：工作区重置洗掉台账后按公开页+列表真值重登（dateCreated 20261125T07:40）"),
    ("vid-ai", "csdn", "published", "171343780", "",
     "用AI工作流批量生产短视频",
     "https://blog.csdn.net/sinat_32396037/article/details/171343780",
     "2026-11-25 07:42:27",
     "11-25 补账：按 csdn 列表 postTime 真值重登"),
    ("quotedeck", "bilibili", "published", "54071707", "",
     "QuoteDeck：保险代理的专业报价工具",
     "https://www.bilibili.com/read/cv54071707",
     "2026-11-25 07:22",
     "11-25 补账：按空间 API publish_time 真值重登"),
    ("chatsell-co", "bilibili2", "published", "54071853", "",
     "Chatsell.co：为WooCommerce店铺提供自然语言产品搜索和即时答案的AI聊天机器人",
     "https://www.bilibili.com/read/cv54071853",
     "2026-11-25 07:55",
     "11-25 补账：按空间 API publish_time 真值重登（标题 49 字，接口只回前 30 字）"),
    ("betting-tips", "wechat", "published", "2247485067", "",
     "Betting Tips：为足球、篮球、网球、美式足球和棒球爱好者提供每日专家投注建议",
     "",
     "2026-11-25 08:15",
     "11-25 补账：群发后 appmsgid=2247485067（已发表列表出现新记录、标题一致）"),
    # 阿里云：已提交但平台显示内容审核中 ⇒ auditing，published_at 必须为空
    ("stan", "aliyun", "auditing", "1788898", "1733430",
     "解析创作者平台收入数据：支付网关直读证据链",
     "https://developer.aliyun.com/article/1788898",
     None,
     "11-25 提交成功 aid=1788898，平台显示「内容审核中」⇒ auditing，待公开页回读再转 published"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    plan = []
    for cid, plat, state, rid, did, title, url, pat, note in FACTS:
        row = con.execute(
            "select state, remote_id, published_at from platform_posts where case_id=? and platform=?",
            (cid, plat),
        ).fetchone()
        action = "UPDATE" if row else "INSERT"
        plan.append((cid, plat, action, state, rid, pat, dict(row) if row else None))

    print("=== 计划 ===")
    for cid, plat, action, state, rid, pat, old in plan:
        old_s = f"{old.get('state')}/{old.get('remote_id')}/{old.get('published_at')}" if old else "(无行)"
        print(f"  {action:6} {cid:14} {plat:12} -> {state:9} {rid:10} {str(pat):20} | 旧: {old_s}")

    if not a.apply:
        print("\n(干跑，未写盘。加 --apply 执行)")
        con.close()
        return 0

    # ---- db：单条写 ----
    for cid, plat, state, rid, did, title, url, pat, note in FACTS:
        cur = con.execute(
            "update platform_posts set state=?, remote_id=?, title=?, url=?, "
            "draft_id=coalesce(nullif(?,''), draft_id), "
            "published_at=?, note=?, updated_at=? "
            "where case_id=? and platform=?",
            (state, rid, title, url, did, pat, note, STAMP, cid, plat),
        )
        if cur.rowcount == 0:
            con.execute(
                "insert into platform_posts (case_id, platform, state, remote_id, url, title, "
                "draft_id, attempt, published_at, updated_at, note) "
                "values (?,?,?,?,?,?,?,0,?,?,?)",
                (cid, plat, state, rid, url, title, did, pat, STAMP, note),
            )
            print(f"  INSERT {cid}/{plat}")
        else:
            print(f"  UPDATE {cid}/{plat} rows={cur.rowcount}")
    con.commit()

    # ---- manifest：单条合并，其余键不动 ----
    def merge(m):
        changed = []
        for cid, plat, state, rid, did, title, url, pat, note in FACTS:
            slot = m.setdefault(cid, {})
            rec = slot.get(plat) or {}
            rec.update({
                "state": state,
                "remote_id": rid,
                "title": title,
                "url": url or rec.get("url", ""),
                "at": rec.get("at") or STAMP,
                "note": note,
            })
            if did:
                rec["draft_id"] = did
            if pat:
                rec["published_at"] = pat
            else:
                rec["published_at"] = ""
            rec.setdefault("attempt", 0)
            slot[plat] = rec
            changed.append(f"{cid}/{plat}")
        print("  manifest 更新:", ", ".join(changed))
        return m

    atomicio.update_json(MANIFEST, merge)

    # ---- 回读 ----
    print("\n=== 回读 db ===")
    for cid, plat, *_ in FACTS:
        r = con.execute(
            "select state, remote_id, draft_id, published_at, substr(title,1,26) t "
            "from platform_posts where case_id=? and platform=?", (cid, plat)
        ).fetchone()
        print(f"  {cid:14} {plat:12} {r['state']:9} {str(r['remote_id']):11} {str(r['draft_id'] or ''):9} {str(r['published_at']):18} | {r['t']}")
    con.close()

    print("\n=== 回读 manifest ===")
    m = atomicio.read_json(MANIFEST, {})
    for cid, plat, *_ in FACTS:
        rec = (m.get(cid) or {}).get(plat) or {}
        print(f"  {cid:14} {plat:12} {rec.get('state','?'):9} {str(rec.get('remote_id','')):11} {str(rec.get('published_at','')):18}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
