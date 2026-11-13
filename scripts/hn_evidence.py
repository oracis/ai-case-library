#!/usr/bin/env python
"""HN 线索的人工证据登记表（data/hn_evidence.json）。

为什么单独一张表，而不是把证据写回线索池：
- `data/hn_leads.json` 是**自动采集产物**，重采会整条覆盖；人工批注混进去
  迟早被冲掉，而且再也没法分辨「哪句是机器抓的、哪句是人填的」。
- 证据要能单独审计、单独 diff、单独回滚。它决定一条线索有没有资格晋升，
  比线索本身更重要。
- 晋升只认这张表，不认「标题看起来不错」。没登记 = 没证据 = 不晋升。

三条硬规则（都是为了不虚标）：
1. `verification` 不手填，由 `verify_rules.best_supported_level()` 按
   `source_kind` 推导；手填的值与推导不一致时直接判错。
2. 有金额必须同时有 `caliber` 与 `as_of`（时点）。金额不带时点就是一句
   无法复核的话，红线 1 不允许。
3. 没有金额时 `caliber` 只能是 `undisclosed`，且等级最高只到 `founder` ——
   官网首页/官方页面只能证明产品存在或定价，**不能**给「收入已核实」背书。

用法：
    python scripts/hn_evidence.py --list                 # 只读：线索池登记进度
    python scripts/hn_evidence.py --check                # 只读：校验已登记条目
    python scripts/hn_evidence.py --set hn-50018817 \
        --url https://example.com/pricing --kind homepage \
        --text tmp/hn_md/hn-50018817.md --note "定价页可查，未披露收入"
    python scripts/hn_evidence.py --set hn-50018817 \
        --url https://example.com/about --kind official \
        --revenue 12000 --caliber mrr --as-of 2026-11-13
    python scripts/hn_evidence.py --promote hn-50018817 --yes   # 写进 inbox

⛔ 本脚本永不写主候选池 `data/candidates.json`。
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import atomicio          # noqa: E402  原子写 + 文件锁
import verify_rules as VR  # noqa: E402  证据等级与口径枚举的唯一来源

LEADS_PATH = os.path.join(ROOT, "data", "hn_leads.json")
EVIDENCE_PATH = os.path.join(ROOT, "data", "hn_evidence.json")
INBOX_PATH = os.path.join(ROOT, "data", "inbox.json")

#: 社区验证下限。HN 的 points 是发帖时快照，不是现值，但它是「有人看过」
#: 的最低成本证据；低于这个数的 Show HN 连讨论都没有，不值得占人工回源预算。
HN_MIN_POINTS = 50

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
URL_RE = re.compile(r"^https?://[^\s]+$", re.I)


def _today():
    return datetime.now().strftime("%Y-%m-%d")


def _rel(path):
    try:
        return os.path.relpath(path, ROOT).replace("\\", "/")
    except ValueError:
        # 测试把路径重定向到别的盘符（C: vs D:）时 relpath 无解，原样返回即可。
        return path


def _read_json(path, default=None):
    try:
        with io.open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except ValueError as e:
        raise SystemExit("拒绝继续：%s 不是合法 JSON（%s）" % (_rel(path), e))


def load_leads():
    """线索池按 id 建索引。只读。"""
    data = _read_json(LEADS_PATH, [])
    if not isinstance(data, list):
        raise SystemExit("拒绝继续：%s 顶层不是列表" % _rel(LEADS_PATH))
    return {str(c.get("id") or ""): c for c in data if isinstance(c, dict)}


def load_evidence():
    """证据表。文件不存在时返回空表（第一次 --set 才落盘）。"""
    data = _read_json(EVIDENCE_PATH)
    if data is None:
        return {"version": 1, "updated_at": _today(), "items": {}}
    if not isinstance(data, dict) or not isinstance(data.get("items"), dict):
        raise SystemExit("拒绝继续：%s 结构应为 {version, updated_at, items:{}}"
                         % _rel(EVIDENCE_PATH))
    return data


def derive_verification(source_kind, has_revenue):
    """按证据来源推导核实等级。**不读用户填的值**。"""
    lvl = VR.best_supported_level([source_kind])
    # `homepage` 在 SOURCE_TIERS 里是 C 级（只证产品/定价存在，不证收入），
    # 但 `best_supported_level()` 没有为它单列分支 —— 它按「C 级 = founder」
    # 的语义处理。这里只做局部映射，不动全局规则，免得改到存量审计的口径。
    if lvl is None and source_kind == "homepage":
        lvl = "founder"
    if lvl is None:
        return None
    # 没有数字，就没有「数字已被核实」这回事。官网/官方页只证明产品与定价，
    # 把它标成 official 等于替读者完成一次他们没授权我们做的信任背书。
    if not has_revenue and lvl in ("stripe", "official"):
        return "founder"
    return lvl


def validate(item, lead):
    """返回错误列表（空 = 通过）。纯校验，不写盘。"""
    errs = []
    if not lead:
        errs.append("id 不在线索池 data/hn_leads.json（先采集再登记证据）")
    elif int(lead.get("hn_points") or 0) < HN_MIN_POINTS:
        errs.append("HN 分数 %s < %d，社区验证不足"
                    % (lead.get("hn_points"), HN_MIN_POINTS))

    url = str(item.get("evidence_url") or "").strip()
    if not url:
        errs.append("evidence_url 必填")
    elif not URL_RE.match(url):
        errs.append("evidence_url 必须是 http(s) 链接：%s" % url)

    kind = str(item.get("source_kind") or "").strip()
    if kind not in VR.SOURCE_TIER_KEYS:
        errs.append("source_kind 必须是已登记来源类型之一：%s"
                    % "、".join(VR.SOURCE_TIER_KEYS))

    if not DATE_RE.match(str(item.get("checked_at") or "")):
        errs.append("checked_at 必须是 YYYY-MM-DD")

    rev = item.get("revenue")
    cal = str(item.get("caliber") or "").strip()
    if rev is None:
        if cal != "undisclosed":
            errs.append("没有金额时 caliber 必须是 undisclosed（收到 %r）" % cal)
    else:
        if not isinstance(rev, dict):
            errs.append("revenue 必须是 {value, caliber, as_of, currency} 或 null")
        else:
            v = rev.get("value")
            if not isinstance(v, (int, float)) or isinstance(v, bool) or v <= 0:
                errs.append("revenue.value 必须是正数（收到 %r）" % (v,))
            if str(rev.get("caliber") or "") not in VR.CALIBER_KEYS:
                errs.append("revenue.caliber 不在口径枚举里：%r" % (rev.get("caliber"),))
            elif rev.get("caliber") == "undisclosed":
                errs.append("有金额时不能填 undisclosed 口径")
            if not DATE_RE.match(str(rev.get("as_of") or "")):
                errs.append("revenue.as_of 必填且为 YYYY-MM-DD（金额必须带时点）")
        if cal == "undisclosed":
            errs.append("有金额时 caliber 不能是 undisclosed")

    # 等级一致性：文件里的值必须等于推导值（防手改虚标）
    want = derive_verification(kind, rev is not None)
    got = str(item.get("verification") or "")
    if want and got and got != want:
        errs.append("verification 被手改成 %s，但按证据只能到 %s" % (got, want))
    if want is None:
        errs.append("这组证据撑不起任何等级（如仅二手转述），需人工重核")

    tp = str(item.get("text_path") or "").strip()
    if tp and not os.path.exists(os.path.join(ROOT, tp)):
        errs.append("text_path 指向的文件不存在：%s" % tp)
    return errs


def build_item(args, lead):
    """命令行参数 → 一条证据记录（已推导等级）。"""
    rev = None
    if args.revenue is not None:
        rev = {"value": float(args.revenue), "caliber": args.caliber,
               "as_of": args.as_of, "currency": args.currency or "USD"}
    cal = (rev["caliber"] if rev else "undisclosed")
    kind = args.kind
    return {
        "evidence_url": args.url,
        "source_kind": kind,
        "verification": derive_verification(kind, rev is not None),
        "revenue": rev,
        "caliber": cal,
        "text_path": args.text or "",
        "checked_at": args.checked_at or _today(),
        "checked_by": args.checked_by or "human",
        "note": args.note or "",
    }


#: caliber → 候选/案例 metrics 的键名。前端的 `primaryMoney()` 只认
#: arr / mrr / all_time，所以 lifetime 必须落到 all_time，否则榜单沉底。
_METRIC_KEY = {
    "mrr": "mrr", "arr": "arr", "run_rate": "run_rate", "annual": "annual",
    "lifetime": "all_time", "last_30d": "last_30d_revenue",
    "gmv": "gmv", "gross": "gross",
}


def build_inbox_entry(lead, ev):
    """线索 + 证据 → 一条 inbox 记录（harvest_source=hn_evidence）。"""
    rev = ev.get("revenue")
    kind = ev["source_kind"]
    lvl = ev["verification"]
    label = dict((k, lb) for k, lb, _t, _f in VR.SOURCE_TIERS).get(kind, kind)
    if rev:
        key = _METRIC_KEY.get(rev["caliber"], rev["caliber"])
        money = "${:,.0f}".format(rev["value"])
        cal_label = dict((k, lb) for k, lb, _d in VR.CALIBERS).get(
            rev["caliber"], rev["caliber"])
        headline = "%s %s（%s）" % (cal_label, money, VR.LEVEL_LABEL.get(lvl, lvl))
        metrics = {
            key: rev["value"],
            "headline": headline,
            "caliber": rev["caliber"],
            "revenue_status": "disclosed",
            "evidence_url": ev["evidence_url"],
            "metric_note": ("口径：%s %s，时点 %s，来源「%s」（%s 登记）。%s"
                            % (cal_label, money, rev["as_of"],
                               label, ev["checked_at"],
                               ev.get("note") or "")),
        }
    else:
        metrics = {
            "headline": "收入未披露",
            "caliber": "undisclosed",
            "revenue_status": "undisclosed",
            "evidence_url": ev["evidence_url"],
            "metric_note": ("收入未公开；证据为「%s」（%s 登记）。"
                            "正文不得出现任何金额，也不得据此推断规模。%s"
                            % (label, ev["checked_at"], ev.get("note") or "")),
        }

    entry = {
        "id": lead["id"],
        "name": lead.get("name") or lead["id"],
        "name_en": lead.get("name_en") or lead.get("name") or "",
        "origin": lead.get("origin") or "",
        "one_liner": lead.get("one_liner") or "",
        "category": lead.get("category") or "未分类",
        "verification": lvl,
        "metrics": metrics,
        "models": lead.get("models") or [],
        "note": ("HN 线索（%d 分 / %d 评，%s）+ 人工回源：%s。"
                 "证据见 %s；HN 自述与分数均不作收入证据。"
                 % (int(lead.get("hn_points") or 0), int(lead.get("hn_comments") or 0),
                    lead.get("hn_created_at") or "—", label, ev["evidence_url"])),
        "blocking": ("收入未披露：发布前不得补写任何金额" if not rev else
                     "收入为%s口径，发布前人工确认时效与统计范围"
                     % VR.LEVEL_LABEL.get(lvl, lvl)),
        "source_url": lead.get("source_url") or "",
        "website": lead.get("website") or "",
        "harvest_source": "hn_evidence",
        "source_kind": kind,
        "added_at": _today(),
        "hn_id": lead.get("hn_id") or "",
        "hn_role": "lead",
        "hn_title": lead.get("hn_title") or "",
        "hn_points": lead.get("hn_points") or 0,
        "hn_comments": lead.get("hn_comments") or 0,
        "hn_created_at": lead.get("hn_created_at") or "",
        "hn_discussion": lead.get("hn_discussion") or "",
        "hn_flags": lead.get("hn_flags") or [],
        "hidden_urls": lead.get("hidden_urls") or {},
        "hn_evidence": ev,
    }
    return entry


# ---------------------------------------------------------------- 命令

def cmd_list(args):
    leads = load_leads()
    ev = load_evidence()["items"]
    rows = sorted(leads.values(),
                  key=lambda c: (-int(c.get("hn_points") or 0), c.get("id") or ""))
    done = [c for c in rows if c["id"] in ev]
    pend = [c for c in rows if c["id"] not in ev]
    print("线索池 %d 条 ｜ 已登记证据 %d ｜ 未登记 %d"
          % (len(rows), len(done), len(pend)))
    print("-" * 78)
    for c in done:
        e = ev[c["id"]]
        rev = e.get("revenue")
        money = ("$%s %s" % ("{:,.0f}".format(rev["value"]), rev["caliber"])
                 if rev else "收入未披露")
        print("[已登记] %-16s %5d分  %-10s %-10s %s"
              % (c["id"], int(c.get("hn_points") or 0), e["source_kind"],
                 e["verification"], money))
    for c in pend[:args.show]:
        print("[待回源] %-16s %5d分  %s"
              % (c["id"], int(c.get("hn_points") or 0),
                 (c.get("hn_title") or c.get("name") or "")[:52]))
    if len(pend) > args.show:
        print("... 另 %d 条未登记（--show N 调整）" % (len(pend) - args.show))
    return 0


def cmd_check(args):
    leads = load_leads()
    ev = load_evidence()
    bad = 0
    for cid, item in sorted(ev["items"].items()):
        errs = validate(item, leads.get(cid))
        if errs:
            bad += 1
            print("[FAIL] %s" % cid)
            for e in errs:
                print("       - %s" % e)
        else:
            print("[ OK ] %s  %-10s %-10s %s"
                  % (cid, item["source_kind"], item["verification"],
                     "有金额" if item.get("revenue") else "收入未披露"))
    print("\n证据表 %d 条 ｜ 失败 %d" % (len(ev["items"]), bad))
    return 1 if bad else 0


def cmd_set(args):
    leads = load_leads()
    lead = leads.get(args.set)
    if not lead:
        raise SystemExit("拒绝写入：%s 不在线索池" % args.set)
    if args.revenue is not None and not (args.caliber and args.as_of):
        raise SystemExit("拒绝写入：给了 --revenue 就必须同时给 --caliber 与 "
                         "--as-of（金额必须带口径和时点）")
    # 反向也拦：只给口径不给数字，口径就是悬空的 —— 不拦的话 build_item
    # 会把它悄悄改成 undisclosed，等于把一次输入错误洗成一条合法记录。
    if args.revenue is None and args.caliber:
        raise SystemExit("拒绝写入：给了 --caliber 却没有 --revenue"
                         "（口径必须挂在数字上；无收入请留空）")
    item = build_item(args, lead)
    errs = validate(item, lead)
    if errs:
        print("拒绝写入 %s：" % args.set, file=sys.stderr)
        for e in errs:
            print("  - %s" % e, file=sys.stderr)
        return 2

    # atomicio.read_json 对「文件不存在」不兜底（那不是它的职责），
    # 所以首次登记时先原子建一张空表，再走读-改-写。
    if not os.path.isfile(EVIDENCE_PATH):
        atomicio.write_json(EVIDENCE_PATH,
                            {"version": 1, "updated_at": _today(), "items": {}},
                            indent=2, newline="")

    def _tx(obj):
        obj.setdefault("version", 1)
        obj.setdefault("items", {})
        obj["items"][args.set] = item
        obj["updated_at"] = _today()
        return obj

    atomicio.update_json(EVIDENCE_PATH, _tx, indent=2, newline="")
    print("已登记 %s：%s / %s / %s"
          % (args.set, item["source_kind"], item["verification"],
             "有金额" if item["revenue"] else "收入未披露"))
    print("下一步：python scripts/hn_evidence.py --promote %s --yes" % args.set)
    return 0


def cmd_promote(args):
    ids = [x.strip() for x in args.promote.split(",") if x.strip()]
    if not os.path.isfile(INBOX_PATH):
        raise SystemExit("拒绝晋升：inbox 不存在 %s" % _rel(INBOX_PATH))
    leads, ev = load_leads(), load_evidence()["items"]
    todo = []
    for cid in ids:
        lead = leads.get(cid)
        item = ev.get(cid)
        if not item:
            raise SystemExit("拒绝晋升：%s 还没登记证据（先 --set）" % cid)
        errs = validate(item, lead)
        if errs:
            raise SystemExit("拒绝晋升 %s：%s" % (cid, "；".join(errs)))
        todo.append(build_inbox_entry(lead, item))

    def _tx(inbox):
        if not isinstance(inbox, list):
            raise SystemExit("拒绝写入：inbox.json 顶层不是列表")
        pos = {str(x.get("id")): i for i, x in enumerate(inbox) if isinstance(x, dict)}
        added = updated = 0
        for e in todo:
            if e["id"] in pos:
                inbox[pos[e["id"]]] = e
                updated += 1
            else:
                inbox.append(e)
                pos[e["id"]] = len(inbox) - 1
                added += 1
        print("inbox：新增 %d / 覆盖 %d（幂等，重复跑不增条）" % (added, updated))
        return inbox

    atomicio.update_json(INBOX_PATH, _tx, indent=2,
                         newline=atomicio.detect_newline(INBOX_PATH))
    print("已写入 %s（harvest_source=hn_evidence，主候选池未触碰）"
          % _rel(INBOX_PATH))
    print("下一步：python scripts/promote_to_candidates.py --ids %s --write"
          % ",".join(ids))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--list", action="store_true", help="只读：查看登记进度")
    ap.add_argument("--check", action="store_true", help="只读：校验已登记条目")
    ap.add_argument("--set", default="", metavar="ID", help="登记/更新一条证据")
    ap.add_argument("--promote", default="", metavar="ID[,ID]",
                    help="把已登记的线索写进 inbox（须 --yes）")
    ap.add_argument("--yes", action="store_true", help="写盘二次确认")
    ap.add_argument("--url", default="", help="证据链接（官网/定价页/官方帖）")
    ap.add_argument("--kind", default="", help="来源类型：%s" % "、".join(VR.SOURCE_TIER_KEYS))
    ap.add_argument("--revenue", type=float, default=None, help="收入数字（可空）")
    ap.add_argument("--caliber", default="", help="收入口径：%s" % "、".join(VR.CALIBER_KEYS))
    ap.add_argument("--as-of", default="", help="收入时点 YYYY-MM-DD（有金额时必填）")
    ap.add_argument("--currency", default="USD", help="币种，默认 USD")
    ap.add_argument("--text", default="", help="回源正文相对路径（如 tmp/hn_md/<id>.md）")
    ap.add_argument("--note", default="", help="人工备注")
    ap.add_argument("--checked-at", default="", help="登记日 YYYY-MM-DD，默认今天")
    ap.add_argument("--checked-by", default="human", help="登记人，默认 human")
    ap.add_argument("--show", type=int, default=15, help="--list 展示条数")
    # 路径重定向只为测试与演练：默认永远是 data/ 下的正式台账。
    ap.add_argument("--path", default="", help="证据表路径（测试用，默认 data/hn_evidence.json）")
    ap.add_argument("--inbox", default="", help="inbox 路径（测试用，默认 data/inbox.json）")
    ap.add_argument("--leads", default="", help="线索池路径（测试用，默认 data/hn_leads.json）")
    args = ap.parse_args(argv)

    global EVIDENCE_PATH, INBOX_PATH, LEADS_PATH
    if args.path:
        EVIDENCE_PATH = os.path.abspath(args.path)
    if args.inbox:
        INBOX_PATH = os.path.abspath(args.inbox)
    if args.leads:
        LEADS_PATH = os.path.abspath(args.leads)

    if args.promote and not args.yes:
        raise SystemExit("--promote 必须同时给 --yes")
    if args.set:
        return cmd_set(args)
    if args.promote:
        return cmd_promote(args)
    if args.check:
        return cmd_check(args)
    return cmd_list(args)


if __name__ == "__main__":
    sys.exit(main())
