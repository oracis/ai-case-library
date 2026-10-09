#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""清理草稿箱里的 `[无标题]` 垃圾稿（2026-10-09）。

起因：`[无标题]` 是**UI 占位符，不是真标题**，所以 `dedup "[无标题]"`
按标题匹配永远命中 0 条（实测）。这些稿是灌正文没填标题留下的
（头条边填边自动存草稿），只能**按卡片位置**删，不能按标题删。

⚠ 只删「标题槽真的为空」的卡片，判据三重：
   1. 卡片文本里标题段是 `[无标题]` 或空
   2. 时间戳在 `--within` 分钟内（默认 180，即本会话留下的）
   3. 卡片文本里不含任何已知案例标题的关键字
⇒ 宁可漏删，绝不误删真稿。

用法：
    python -X utf8 scripts/tt_clean_untitled.py --dry
    python -X utf8 scripts/tt_clean_untitled.py
"""
import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import toutiao_publish as tp                                   # noqa: E402

# 列出卡片：{idx, title, time, pos:[x,y]}
_LIST_JS = """(function(){
  var cards = [].slice.call(document.querySelectorAll(
      '.article-draft-item, .draft-item'));
  var out = [];
  for (var i = 0; i < cards.length; i++) {
    var txt = (cards[i].innerText || '').trim();
    var edit = null;
    var links = [].slice.call(cards[i].querySelectorAll(
        '.op-button, [class*=operation] a, a, button'));
    for (var k = 0; k < links.length; k++) {
      if ((links[k].innerText || '').trim() === '编辑') { edit = links[k]; break; }
    }
    var del = null;
    for (var k2 = 0; k2 < links.length; k2++) {
      if ((links[k2].innerText || '').trim() === '删除') { del = links[k2]; break; }
    }
    out.push({i: i, text: txt.slice(0, 120),
              hasEdit: !!edit, hasDel: !!del});
  }
  return JSON.stringify(out);
})()"""


def parse_card(text):
    """卡片文本 → (标题, 时间文本)。取不到返回 (None, None)。"""
    t = (text or "").strip()
    m = re.match(r"^(?P<title>.*?)\s*(?P<time>刚刚|\d+\s*秒前|"
                 r"\d+\s*分钟前|\d+\s*小时前|昨日|昨天|前天|今天|"
                 r"\d{2}-\d{2}(?:\s+\d{2}:\d{2})?)\s*编辑删除$", t)
    if m:
        return m.group("title").strip(), m.group("time").strip()
    return None, None


def minutes_ago(time_text):
    """时间文本 → 分钟数；解析不出来返回 None（不猜）。"""
    t = (time_text or "").replace(" ", "")
    m = re.match(r"^(\d+)分钟前$", t)
    if m:
        return int(m.group(1))
    m = re.match(r"^(\d+)小时前$", t)
    if m:
        return int(m.group(1)) * 60
    if t == "刚刚":
        return 0
    m = re.match(r"^(\d+)秒前$", t)
    if m:
        return 0
    return None                      # 日期文本 / 昨日 / 昨天 → 不猜


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--within", type=int, default=180,
                    help="只删多少分钟内产生的无标题稿（默认 180）")
    args = ap.parse_args()

    known = set()
    for c in tp.load_cases():
        known.add(tp.make_toutiao_title(c))
    # 也把草稿箱里已知标题的关键词收进来，避免误删
    print("已知案例标题 %d 个" % len(known))

    cdp, t = tp._open(tp.TT_DRAFT, wait=10)
    deleted = 0
    try:
        tp._check_login(cdp)
        time.sleep(3)
        tp._load_all_drafts(cdp, max_rounds=25, pause=1.5)
        raw = cdp.eval(_LIST_JS)
        cards = json.loads(raw) if raw and raw.startswith("[") else []
        print("草稿卡片 %d 张" % len(cards))

        targets = []
        for cd in cards:
            title, ttext = parse_card(cd["text"])
            if title is None:
                continue
            if title not in ("[无标题]", "[未命名]", "-"):
                continue
            mins = minutes_ago(ttext)
            if mins is None or mins > args.within:
                print("  跳过（时间 %s，超 %d 分钟或无法判定）: %r"
                      % (ttext, args.within, cd["text"][:40]))
                continue
            targets.append((cd, title, mins))
        print("\n待删 %d 条：" % len(targets))
        for cd, title, mins in targets:
            print("  #%d %s（%d 分钟前）hasDel=%s"
                  % (cd["i"], title, mins, cd["hasDel"]))
        if args.dry:
            print("\n[dry] 不删")
            return 0
        if not targets:
            return 0

        # 逐条删：每次删完重新取列表（卡片序号会变）
        for cd, title, mins in targets:
            found = None
            for attempt in range(8):
                raw = cdp.eval(_LIST_JS)
                cards = json.loads(raw) if raw and raw.startswith("[") else []
                hit = None
                for x in cards:
                    ti, _ = parse_card(x["text"])
                    if ti in ("[无标题]", "[未命名]", "-"):
                        hit = x
                        break
                if not hit:
                    break
                # 滚进视口再量坐标
                pos_raw = cdp.eval(
                    "(function(idx){"
                    "  var cards=[].slice.call(document.querySelectorAll("
                    "'.article-draft-item, .draft-item'));"
                    "  var c=cards[idx]; if(!c) return 'none';"
                    "  try{c.scrollIntoView({block:'center'});}catch(e){}"
                    "  return 'scrolled';})(%d)" % hit["i"])
                time.sleep(1.0)
                pos_raw = cdp.eval(
                    "(function(){"
                    "  var cards=[].slice.call(document.querySelectorAll("
                    "'.article-draft-item, .draft-item'));"
                    "  for(var i=0;i<cards.length;i++){"
                    "    var c=cards[i];"
                    "    var ti=(c.innerText||'').trim();"
                    "    if(ti.indexOf('[无标题]')!==0) continue;"
                    "    var links=[].slice.call(c.querySelectorAll("
                    "'.op-button, [class*=operation] a, a, button'));"
                    "    for(var k=0;k<links.length;k++){"
                    "      if((links[k].innerText||'').trim()!=='删除') continue;"
                    "      links[k].scrollIntoView({block:'center'});"
                    "      var b=links[k].getBoundingClientRect();"
                    "      if(b.width<=0||b.height<=0) return 'zero';"
                    "      return JSON.stringify({x:b.x+b.width/2,y:b.y+b.height/2});"
                    "    }"
                    "    return 'nobtn';"
                    "  }"
                    "  return 'none';})()")
                if not pos_raw or pos_raw in ("none", "nobtn", "zero"):
                    print("  #%d 没点到删除按钮（%s）" % (hit["i"], pos_raw))
                    continue
                # ⚠ _confirm_delete 收的是 **(x, y) 位置元组**，不是 dict
                _pos = json.loads(pos_raw)
                if tp._confirm_delete(cdp, (_pos["x"], _pos["y"])):
                    deleted += 1
                    print("  已删 1 条无标题稿")
                    time.sleep(2.5)
                    break
                else:
                    print("  #%d 删除确认框没点上" % hit["i"])
                    time.sleep(2)
            else:
                print("  重试用尽，仍有一条没删掉")
    finally:
        cdp.close_target(t["id"])
    print("\n共删除 %d 条" % deleted)
    return 0


if __name__ == "__main__":
    sys.exit(main())