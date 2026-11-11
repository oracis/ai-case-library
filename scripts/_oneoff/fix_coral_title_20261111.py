#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""把公众号已发表 Coral（appmsgid 2247485052）标题/摘要里的「——」改成「，」。

⚠ 微信修改页标题是**逐字符 diff 编辑器**：content_check 的 title 取
   vm.pageTitle，modify_detail.title_list 来自 vm.modifyTextList；直接改
   字符串不会生成修改记录（POST 里连 title 参数都不带）。必须走平台自己的
   交互：点字符 → 「修改」→ 输入新字 → 确定；再对第二个破折号点「删除」。
   摘要则可全文直改 vm.pageData.digest（wechat_set_digest.py 已验证）。

缺省干跑：只做编辑态修改 + preview=1 校验 + 打印 POST 参数，**不 submit**。
--yes 真提交（消耗 1 次修改机会，可能要管理员扫码），随后独立回读
appmsgpublish 原始 title/digest + remain_modify_count 才算数。
"""
import argparse
import json
import os
import sys
import time
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
os.environ.setdefault("LOCALAPPDATA",
                      os.path.join(os.path.expanduser("~"), "AppData", "Local"))
os.environ.setdefault("ProgramFiles", r"C:\Program Files")

import wechat_publish as wp       # noqa: E402
import wechat_sync as ws          # noqa: E402
import wechat_set_digest as wsd   # noqa: E402

APPMSGID = "2247485052"
OLD_TITLE = "Coral：帮你把开源 AI agent 部署、托管、接上各种业务系统——卖的是「配置」，不是模型"
NEW_TITLE = "Coral：帮你把开源 AI agent 部署、托管、接上各种业务系统，卖的是「配置」，不是模型"
OLD_DIGEST = "帮你把开源 AI agent 部署、托管、接上各种业务系统——卖的是「配置」，不是模型"
NEW_DIGEST = "帮你把开源 AI agent 部署、托管、接上各种业务系统，卖的是「配置」，不是模型"

HOOK_JS = r"""(function(){
  window.__wb_posts=[];
  var O=XMLHttpRequest.prototype.open, S=XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open=function(m,u){this.__wb_url=u;return O.apply(this,arguments);};
  XMLHttpRequest.prototype.send=function(b){
    try{window.__wb_posts.push({url:String(this.__wb_url||''),
      body:String(b||'').slice(0,20000)});}catch(e){}
    return S.apply(this,arguments);
  };
  var F=window.fetch;
  if(F){window.fetch=function(u,o){
    try{window.__wb_posts.push({url:String(u||''),
      body:String((o&&o.body)||'').slice(0,20000)});}catch(e){}
    return F.apply(this,arguments);
  };}
  return 'HOOKED';
})()"""

FIND_VM_JS = r"""(function(){
  var all=document.querySelectorAll('body *'), seen=[], stack=[];
  for(var i=0;i<all.length;i++) if(all[i].__vue__) stack.push(all[i].__vue__);
  while(stack.length){
    var vm=stack.pop(); if(!vm||seen.indexOf(vm)>=0) continue; seen.push(vm);
    if(vm.pageData && typeof vm.pageData.digest==='string' && typeof vm.request==='function'){
      window.__wb_vm=vm; return 'FOUND';
    }
    var ch=vm.$children||[];
    for(var j=0;j<ch.length;j++) stack.push(ch[j]);
    if(vm.$parent) stack.push(vm.$parent);
  }
  return 'NOTFOUND';
})()"""

DASH_JS = r"""(function(){
  var els=Array.from(document.querySelectorAll('span.character.page_title_char'))
    .filter(function(e){return e.textContent==='\u2014';});
  return JSON.stringify(els.map(function(e){
    var r=e.getBoundingClientRect();
    return {x:Math.round(r.left+r.width/2), y:Math.round(r.top+r.height/2),
            w:r.width, h:r.height};
  }));
})()"""

MENU_JS = r"""(function(label){
  var els=Array.from(document.querySelectorAll('.weui-desktop-popover-menu__item'))
    .filter(function(e){return (e.innerText||'').trim()===label;});
  var vis=els.filter(function(e){var r=e.getBoundingClientRect();return r.width>0&&r.height>0;});
  if(!vis.length) return JSON.stringify({found:false});
  var r=vis[0].getBoundingClientRect();
  return JSON.stringify({found:true,x:Math.round(r.left+r.width/2),
                         y:Math.round(r.top+r.height/2)});
})(%s)"""

INPUT_JS = r"""(function(){
  var els=Array.from(document.querySelectorAll('input,textarea'))
    .filter(function(e){var r=e.getBoundingClientRect();return r.width>0&&r.height>0;});
  return JSON.stringify(els.slice(0,8).map(function(e){
    var r=e.getBoundingClientRect();
    return {tag:e.tagName,cls:String(e.className).slice(0,80),
            val:(e.value||'').slice(0,60),
            x:Math.round(r.left+r.width/2), y:Math.round(r.top+r.height/2)};
  }));
})()"""

SET_INPUT_JS = r"""(function(ch){
  var els=Array.from(document.querySelectorAll('input,textarea'))
    .filter(function(e){var r=e.getBoundingClientRect();return r.width>0&&r.height>0;});
  if(!els.length) return 'NO_INPUT';
  var e=els[0], vm=window.__wb_vm;
  e.focus(); e.value=ch;
  e.dispatchEvent(new Event('input',{bubbles:true}));
  e.dispatchEvent(new Event('change',{bubbles:true}));
  if(vm) vm.textModifyingAfter=ch;
  return JSON.stringify({val:e.value, after:vm?vm.textModifyingAfter:null});
})(%s)"""

CONFIRM_JS = r"""(function(){
  var els=Array.from(document.querySelectorAll('a,button,span,div,li'))
    .filter(function(e){
      var t=(e.innerText||'').trim();
      return (t==='确定'||t==='确认'||t==='保存'||t==='完成') && e.children.length===0;
    }).filter(function(e){var r=e.getBoundingClientRect();return r.width>0&&r.height>0;});
  if(!els.length) return JSON.stringify({found:false});
  var r=els[0].getBoundingClientRect();
  return JSON.stringify({found:true,txt:els[0].innerText.trim(),
                         x:Math.round(r.left+r.width/2),y:Math.round(r.top+r.height/2)});
})()"""

STATE_JS = r"""(function(){
  var vm=window.__wb_vm;
  var titleEl=document.querySelector('.appmsg_modify_article_title .title_body');
  return JSON.stringify({
    title_dom:titleEl?(titleEl.innerText||'').trim():'',
    pageTitle:vm?String(vm.pageTitle||''):'',
    digest:vm?String(vm.pageData.digest||''):'',
    currTitle:vm?vm.currModifyTitleCount:null,
    modifyList:vm?JSON.stringify(vm.modifyTextList||[]).slice(0,800):'',
    modifyList_len:vm?(vm.modifyTextList||[]).length:null
  });
})()"""

SET_DIGEST_JS = r"""(function(d){
  var vm=window.__wb_vm; if(!vm) return 'NO_VM';
  vm.pageData.digest=d; vm.digestTextCount=d.length;
  return 'SET digest len='+d.length;
})(%s)"""

PREVIEW_JS = r"""(function(){
  var vm=window.__wb_vm; if(!vm) return 'NO_VM';
  window.__wb_result=null;
  try{
    // preview=0 ⇒ 与 submit() 的第一步同参数形状，但不 POST action=submit，
    // 因此不消耗修改机会；用它来验 title/digest/modify_detail 是否生成正确。
    vm.request(false,function(resp){
      window.__wb_result={ok:true,resp:JSON.parse(JSON.stringify(resp||{}))};
    },function(e){window.__wb_result={ok:false,err:String(e).slice(0,300)};});
    return 'CALLED';
  }catch(e){return 'THROW '+e.name+':'+String(e.message).slice(0,200);}
})()"""

SUBMIT_JS = r"""(function(){
  var vm=window.__wb_vm; if(!vm) return 'NO_VM';
  try{ vm.submit(); return 'SUBMIT_CALLED'; }
  catch(e){ return 'THROW '+e.name+':'+String(e.message).slice(0,200); }
})()"""

QR_JS = r"""(function(){
  var bt=document.body.innerText||'';
  return JSON.stringify({qr:/扫码|二维码|管理员验证|安全验证/.test(bt),
                         tail:bt.slice(-180)});
})()"""


def jload(raw):
    try:
        return json.loads(raw if isinstance(raw, str) else str(raw or ""))
    except Exception:
        return {}


def norm(s):
    """DOM innerText 用 NBSP 渲染空格，服务端 title 用普通空格 ⇒ 比对前归一。"""
    return (s or "").replace("\u00a0", " ").replace("\u200b", "").strip()


def click(cdp, x, y):
    cdp.send("Input.dispatchMouseEvent",
             {"type": "mouseMoved", "x": x, "y": y})
    time.sleep(0.1)
    for typ in ("mousePressed", "mouseReleased"):
        cdp.send("Input.dispatchMouseEvent",
                 {"type": typ, "x": x, "y": y, "button": "left", "clickCount": 1})
        time.sleep(0.1)
    time.sleep(0.45)


def menu_click(cdp, label):
    m = jload(cdp.eval(MENU_JS % json.dumps(label, ensure_ascii=False)))
    if not m.get("found"):
        print("  ✗ 找不到可见菜单项「%s」" % label)
        return False
    click(cdp, m["x"], m["y"])
    print("  ✓ 点菜单「%s」(%s,%s)" % (label, m["x"], m["y"]))
    return True


def wait_vm(cdp):
    for _ in range(10):
        time.sleep(2.5)
        if cdp.eval(FIND_VM_JS) == "FOUND":
            return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true", help="真提交（消耗 1 次修改机会）")
    ap.add_argument("--wait", type=int, default=300, help="提交后等扫码/落库的最长秒数")
    args = ap.parse_args()

    wp._autostart_chrome_if_needed(port=9222)
    cdp = wp.CDP(9222)
    tid, token = wp._connect_mp(cdp)
    if not token:
        print("✗ 拿不到 token（需人工扫码登录）")
        return 2
    cdp.connect_target(tid)
    cdp.send("Page.enable")

    before = wsd.remote_row(cdp, token, APPMSGID) or {}
    print("[基线] title  =", repr(before.get("title", "")))
    print("       digest =", repr(before.get("digest", "")))
    print("       remain_modify_count =", before.get("remain_modify_count"),
          "｜ can_modify =", before.get("can_modify"))
    if (before.get("title") == NEW_TITLE
            and (before.get("digest") or "").strip() == NEW_DIGEST):
        print("✓ 平台侧标题与摘要已是目标值，幂等停住，不重复提交")
        return 0

    cdp.send("Page.navigate", {"url": wsd.modify_url(APPMSGID, token)})
    if not wait_vm(cdp):
        print("✗ 找不到修改页 Vue 组件")
        return 2
    print("[hook]", cdp.eval(HOOK_JS))
    st = jload(cdp.eval(STATE_JS))
    print("[编辑态基线]", json.dumps(st, ensure_ascii=False)[:500])
    if norm(st.get("title_dom")) != norm(OLD_TITLE):
        print("✗ 标题 DOM 与预期不一致，停住（不猜）")
        return 2

    # ① 第一个破折号 → 「修改」成逗号
    dashes = jload(cdp.eval(DASH_JS))
    print("\n[①] 标题破折号字符数", len(dashes))
    if len(dashes) != 2:
        print("✗ 期望 2 个破折号，实际 %d ⇒ 停住" % len(dashes))
        return 2
    click(cdp, dashes[0]["x"], dashes[0]["y"])
    if not menu_click(cdp, "修改"):
        return 2
    time.sleep(0.6)
    print("  可见输入:", str(cdp.eval(INPUT_JS))[:400])
    print("  填入「，」:", cdp.eval(SET_INPUT_JS % json.dumps("，", ensure_ascii=False)))
    time.sleep(0.4)
    conf = jload(cdp.eval(CONFIRM_JS))
    print("  确认控件:", conf)
    if conf.get("found"):
        click(cdp, conf["x"], conf["y"])
    else:
        cdp.send("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Enter",
                                           "code": "Enter", "windowsVirtualKeyCode": 13})
        cdp.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Enter",
                                           "code": "Enter", "windowsVirtualKeyCode": 13})
    time.sleep(0.8)
    st = jload(cdp.eval(STATE_JS))
    print("  改后:", json.dumps(st, ensure_ascii=False)[:600])

    # ② 剩下的那个破折号 → 「删除」
    dashes = jload(cdp.eval(DASH_JS))
    print("\n[②] 剩余破折号字符数", len(dashes))
    if len(dashes) != 1:
        print("✗ 期望剩 1 个破折号，实际 %d ⇒ 停住" % len(dashes))
        return 2
    click(cdp, dashes[0]["x"], dashes[0]["y"])
    if not menu_click(cdp, "删除"):
        return 2
    time.sleep(0.8)
    st = jload(cdp.eval(STATE_JS))
    print("  删除后:", json.dumps(st, ensure_ascii=False)[:700])

    # ③ 摘要全文直改
    print("\n[③ 摘要]", cdp.eval(SET_DIGEST_JS % json.dumps(NEW_DIGEST, ensure_ascii=False)))
    time.sleep(0.5)

    st = jload(cdp.eval(STATE_JS))
    print("\n[终态编辑态]", json.dumps(st, ensure_ascii=False)[:800])
    # ⚠ DOM innerText 会保留被删字符（渲染成删除线），pageTitle 也不随 UI 变；
    #   真判据是逐字修改记录 + preview 请求里的 title（getNewContent 算出的新标题）。
    ml = jload(st.get("modifyList") or "[]")
    if not isinstance(ml, list) or len(ml) != 2:
        print("✗ 标题修改记录应为 2 条，实际 %s ⇒ 停住" % len(ml))
        return 2
    ok_replace = any(m.get("index") == 938 and m.get("modifyType") == 1
                     and m.get("afterText") == "，" for m in ml)
    ok_delete = any(m.get("index") == 939 and m.get("modifyType") == 0 for m in ml)
    if not (ok_replace and ok_delete):
        print("✗ 修改记录内容不符（replace=%s delete=%s）⇒ 停住"
              % (ok_replace, ok_delete))
        return 2
    if norm(st.get("digest")) != norm(NEW_DIGEST):
        print("✗ 编辑态摘要 ≠ 目标，停住不提交")
        return 2
    if (st.get("currTitle") or 0) > 3:
        print("✗ 标题修改字数 %s > 3，停住" % st["currTitle"])
        return 2
    print("  ✓ 逐字记录 2 条（938 替换→「，」/ 939 删除）｜currTitle=%s｜摘要已改"
          % st.get("currTitle"))

    # ④ preview 校验（不消耗次数）
    print("\n[④ preview]", cdp.eval(PREVIEW_JS))
    for _ in range(15):
        time.sleep(1)
        r = jload(cdp.eval(r"JSON.stringify(window.__wb_result||{})"))
        if r:
            print("  响应:", json.dumps(r, ensure_ascii=False)[:600])
            break
    posts = jload(cdp.eval(r"JSON.stringify(window.__wb_posts||[])"))
    keys, kv = [], {}
    for i, p in enumerate(posts if isinstance(posts, list) else []):
        k2 = list(urllib.parse.parse_qs(p.get("body", ""), keep_blank_values=True))
        print("    POST[%d] %-72s keys=%s"
              % (i, str(p.get("url", ""))[:72], k2[:14]))
    print("  vm.request 源码:",
          str(cdp.eval("String(window.__wb_vm.request).slice(0,1800)"))[:1900])
    cc = [p for p in (posts if isinstance(posts, list) else [])
          if "content_check" in str(p.get("url", ""))]
    print("  content_check 请求条数", len(cc), "｜总 POST", len(posts))
    if cc:
        kv = urllib.parse.parse_qs(cc[-1].get("body", ""), keep_blank_values=True)
        keys = list(kv)
        print("  content_check 参数键:", keys)
        print("  title  =", repr(kv.get("title", [""])[0][:120]))
        print("  digest =", repr(kv.get("digest", [""])[0][:120]))
        print("  modify_detail =", kv.get("modify_detail", [""])[0][:400])
    if not cc:
        print("✗ 没抓到 content_check 请求 ⇒ 停住不提交")
        return 2
    if "title" in keys and norm(kv.get("title", [""])[0]) != norm(NEW_TITLE):
        print("✗ content_check 的 title 不是目标标题 ⇒ 停住不提交")
        return 2
    if "digest" in keys and norm(kv.get("digest", [""])[0]) != norm(NEW_DIGEST):
        print("✗ content_check 的 digest 不是目标摘要 ⇒ 停住不提交")
        return 2
    if r and not r.get("ok"):
        print("✗ content_check 走 error 回调 ⇒ 停住不提交")
        return 2
    # 实测该版本修改页的 content_check 只带 content（无 title/modify_detail），
    # 属平台实现细节，不能据此判定「修改记录没生成」。真判据 = 逐字记录 +
    # 提交后的独立回读（项目红线：脚本自报成功一律不算数）。
    print("  ⚠ 该版本 content_check 只带 content；逐字记录已在 vm.modifyTextList，"
          "真判据改为提交后独立回读")

    if not args.yes:
        print("\n[干跑] 编辑态已完成、preview 已过，未调 submit、未消耗修改机会。")
        print("       确认无误后加 --yes 真提交。")
        return 0

    # ⑤ 真提交
    print("\n⚠ 正在提交修改。若弹出管理员扫码请立即用微信扫一次（最多等 %d 秒）。"
          % args.wait)
    print("[submit]", cdp.eval(SUBMIT_JS))
    deadline = time.time() + args.wait
    row, warned = None, False
    while time.time() < deadline:
        time.sleep(3)
        row = wsd.remote_row(cdp, token, APPMSGID)
        if row and row.get("title") == NEW_TITLE \
                and (row.get("digest") or "").strip() == NEW_DIGEST:
            break
        st2 = jload(cdp.eval(QR_JS))
        if st2.get("qr") and not warned:
            print("  [等待] 检测到扫码/验证弹窗，请扫码…")
            warned = True
    else:
        print("✗ %d 秒内没回读到新标题/摘要。别原地重跑，先查已发表列表。" % args.wait)

    if row:
        print("\n[独立终态回读]")
        print("  title  =", repr(row.get("title", "")))
        print("  digest =", repr(row.get("digest", "")))
        print("  remain_modify_count =", row.get("remain_modify_count"),
              "｜ modify_status =", row.get("modify_status"),
              "｜ appmsg_modified =", row.get("appmsg_modified"))
        print("  modify_wording =", repr(row.get("modify_wording")))
        ok = (row.get("title") == NEW_TITLE
              and (row.get("digest") or "").strip() == NEW_DIGEST)
        print("[结论]", "PASS" if ok else "UNCONFIRMED")
        return 0 if ok else 1
    return 1


if __name__ == "__main__":
    sys.exit(main() or 0)
