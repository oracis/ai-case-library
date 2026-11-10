#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""改公众号**已发表文章**的摘要（2026-11-10 新版修改页）。

⚠ 背景：公众号「已发表文章修改页」已改成 Vue 逐字符 diff 编辑器，
   旧版 `#js_description` textarea 不存在；点单字只会弹「修改/删除」且
   一次只允许替换 1 个字。实测提交链路是
       POST /cgi-bin/masssendmodify?action=content_check  (digest=最终摘要全文)
       POST /cgi-bin/masssendmodify?action=submit         (seq)
   前端组件 `pageData.digest` 就是 content_check 的 digest 来源。
   页面 URL **必须带 token**，否则显示「请重新登录」（不是掉登录）。

用法（缺省干跑：只改前端编辑态 + preview=1 校验，随后恢复，不提交）：
    NO_PROXY='*' CHROME_HEADED=1 /c/Python315/python.exe -X utf8 \
        scripts/wechat_set_digest.py --appmsgid 2247485037 --text "中文摘要"
    # 真提交：加 --yes（消耗该篇 3 次修改机会中的 1 次，可能要管理员扫码）
    ... --yes --wait 300

判据：独立回读 appmsgpublish 原始 publish_info.appmsg_info[0].digest 逐字命中，
     且 remain_modify_count 递减；脚本自报成功一律不算数。
"""
import argparse
import io
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
os.environ.setdefault("LOCALAPPDATA",
                      os.path.join(os.path.expanduser("~"), "AppData", "Local"))
os.environ.setdefault("ProgramFiles", r"C:\Program Files")

import wechat_publish as wp           # noqa: E402
import wechat_sync as ws              # noqa: E402


def modify_url(appmsgid, token, appmsg_id=None, modify_version=1):
    """改过一次后再进必须带 appmsgId + modify_version，否则是「内容已修改」空壳页。

    2026-11-10 实测：文章改过摘要后，只给 appmsgid/idx/token 会拿到空壳
    （vm.content=0、pageTitle 为空）；补 `&appmsgId=2247485937&modify_version=1`
    才加载出真实标题/正文/摘要。`appmsg_id` 是编辑页 URL 里的那个 id，
    不是群发 appmsgid；留空时走首次修改的旧入口（仍可用）。
    """
    u = ("https://mp.weixin.qq.com/cgi-bin/masssendmodify?action=edit_new"
         "&appmsgid=%s&idx=1&lang=zh_CN&token=%s" % (appmsgid, token))
    if appmsg_id:
        u += "&appmsgId=%s&modify_version=%d" % (appmsg_id, modify_version)
    return u


FIND_VM_JS = r"""(function(){
  var all=document.querySelectorAll('body *'), seen=[], stack=[];
  for(var i=0;i<all.length;i++) if(all[i].__vue__) stack.push(all[i].__vue__);
  while(stack.length){
    var vm=stack.pop(); if(!vm||seen.indexOf(vm)>=0) continue; seen.push(vm);
    if(vm.pageData && typeof vm.pageData.digest==='string' && typeof vm.request==='function'){
      window.__digest_vm = vm;
      return JSON.stringify({found:true, digest:vm.pageData.digest,
        max:vm.maxDigestTextCount, count:vm.digestTextCount,
        has_submit:typeof vm.submit==='function'});
    }
    var ch=vm.$children||[];
    for(var j=0;j<ch.length;j++) stack.push(ch[j]);
    if(vm.$parent) stack.push(vm.$parent);
  }
  return JSON.stringify({found:false});
})()"""

SET_JS = r"""(function(text){
  var vm=window.__digest_vm; if(!vm) return 'NO_VM';
  window.__digest_orig = vm.pageData.digest;
  vm.pageData.digest = text;
  vm.digestTextCount = text.length;
  return 'SET len=' + text.length;
})(%s)"""

DOM_JS = r"""(function(){
  var box=document.querySelector('.appmsg_modify_cd__digest_cnt');
  var cnt=document.querySelector('.appmsg_modify_cd__count');
  var vm=window.__digest_vm;
  return JSON.stringify({dom:box?box.innerText:'', counter:cnt?cnt.innerText:'',
    data:vm?vm.pageData.digest:''});
})()"""

PREVIEW_JS = r"""(function(){
  var vm=window.__digest_vm; if(!vm) return 'NO_VM';
  window.__preview_result=null;
  try{
    vm.request(true, function(resp){
      window.__preview_result={ok:true,resp:JSON.parse(JSON.stringify(resp||{}))};
    }, function(){});
    return 'CALLED';
  }catch(e){ return 'THROW '+e.name+':'+String(e.message).slice(0,200); }
})()"""

SUBMIT_JS = r"""(function(){
  var vm=window.__digest_vm; if(!vm) return 'NO_VM';
  window.__submit_called=true;
  try{ vm.submit(); return 'SUBMIT_CALLED'; }
  catch(e){ return 'THROW '+e.name+':'+String(e.message).slice(0,200); }
})()"""

STATE_JS = r"""(function(){
  var bt=document.body.innerText||'';
  return JSON.stringify({qr:/扫码|二维码|管理员验证|安全验证/.test(bt),
    tail:bt.slice(-260), submit_called:!!window.__submit_called});
})()"""

RESTORE_JS = r"""(function(){
  var vm=window.__digest_vm;
  if(!vm||typeof window.__digest_orig==='undefined') return 'NOOP';
  vm.pageData.digest=window.__digest_orig;
  vm.digestTextCount=window.__digest_orig.length;
  return 'RESTORED';
})()"""


def jload(raw):
    raw = raw if isinstance(raw, str) else str(raw or "")
    try:
        return json.loads(raw)
    except Exception:
        return {}


def remote_row(cdp, token, appmsgid):
    """独立回读：appmsgpublish 原始 publish_info.appmsg_info[0]（含 digest）。"""
    url = ("https://mp.weixin.qq.com/cgi-bin/appmsgpublish?sub=list"
           "&search_field=null&begin=0&count=20&token=%s"
           "&lang=zh_CN&f=json&ajax=1" % token)
    d = ws._xhr_json(cdp, url)
    if not isinstance(d, dict):
        return None
    pp = d.get("publish_page")
    if isinstance(pp, str):
        try:
            pp = json.loads(pp)
        except Exception:
            return None
    for it in (pp or {}).get("publish_list") or []:
        pi = it.get("publish_info")
        if isinstance(pi, str):
            try:
                pi = json.loads(pi)
            except Exception:
                continue
        for a in (pi or {}).get("appmsg_info") or []:
            if str(a.get("appmsgid") or "") == str(appmsgid):
                return a
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--appmsgid", required=True)
    ap.add_argument("--text")
    ap.add_argument("--text-file")
    ap.add_argument("--appmsg-id", help="再次修改必填：编辑页 URL 里的 appmsgId（非群发 id）")
    ap.add_argument("--yes", action="store_true", help="真提交（消耗 1 次修改机会）")
    ap.add_argument("--wait", type=int, default=300, help="提交后等扫码/落库的最长秒数")
    args = ap.parse_args()

    text = args.text
    if args.text_file:
        with io.open(args.text_file, encoding="utf-8") as f:
            text = f.read().strip()
    if not text:
        print("✗ 缺 --text / --text-file")
        return 2
    if len(text) > 120:
        print("✗ 摘要 %d 字 > 120，拒绝" % len(text))
        return 2
    print("[摘要] %d 字｜%s" % (len(text), text[:60] + "…"))

    wp._autostart_chrome_if_needed(port=9222)
    cdp = wp.CDP(9222)
    tid, token = wp._connect_mp(cdp)
    if not token:
        print("✗ 拿不到 token（需人工扫码登录）")
        return 2
    cdp.connect_target(tid)
    cdp.send("Page.enable")

    before = remote_row(cdp, token, args.appmsgid)
    print("[基线] digest = %r" % ((before or {}).get("digest", "")))
    print("       remain_modify_count = %s ｜ modify_status = %s"
          % ((before or {}).get("remain_modify_count"),
             (before or {}).get("modify_status")))
    if before and (before.get("digest") or "").strip() == text.strip():
        print("✓ 平台侧已经是该摘要，无需重复提交（幂等停住）")
        return 0

    cdp.send("Page.navigate", {"url": modify_url(args.appmsgid, token,
                                                 args.appmsg_id)})
    time.sleep(7)
    vm = jload(cdp.eval(FIND_VM_JS))
    print("[Vue] %s" % json.dumps({k: v for k, v in vm.items()
                                  if k != "digest"}, ensure_ascii=False))
    if not vm.get("found"):
        print("✗ 找不到持有 pageData.digest 的组件；页面可能又改版")
        return 2
    print("[Vue] 原 digest 与基线一致:", vm.get("digest") == (before or {}).get("digest"))

    print("[写入编辑态]", cdp.eval(SET_JS % json.dumps(text, ensure_ascii=False)))
    time.sleep(1.2)
    dom = jload(cdp.eval(DOM_JS))
    print("[DOM] 逐字一致=%s ｜ 计数=%s" % (dom.get("dom") == text, dom.get("counter")))
    if dom.get("dom") != text:
        print("✗ 编辑态 DOM 不一致，停住")
        cdp.eval(RESTORE_JS)
        return 2

    # 干跑：只做 preview 校验后恢复
    print("[preview]", cdp.eval(PREVIEW_JS))
    for _ in range(15):
        time.sleep(1)
        r = jload(cdp.eval(r"JSON.stringify(window.__preview_result||{})"))
        if r:
            print("[preview 响应]", json.dumps(r, ensure_ascii=False)[:500])
            break
    if not args.yes:
        print("[干跑] 已恢复，不提交：", cdp.eval(RESTORE_JS))
        print("       （确认无误后加 --yes 真提交）")
        return 0

    # 真提交：前端 submit() 自己走 content_check → submit
    print("\n⚠ 正在提交修改。若窗口弹出管理员扫码，请立即用微信扫一次（最多等 %d 秒）。"
          % args.wait)
    print("[submit]", cdp.eval(SUBMIT_JS))
    deadline = time.time() + args.wait
    last = None
    warned_qr = False
    while time.time() < deadline:
        time.sleep(3)
        row = remote_row(cdp, token, args.appmsgid)
        last = row
        if row and (row.get("digest") or "").strip() == text.strip():
            print("\n✓ 平台侧 digest 已逐字命中新中文摘要")
            break
        st = jload(cdp.eval(STATE_JS))
        if st.get("qr") and not warned_qr:
            print("  [等待] 检测到扫码/验证弹窗，请扫码后继续等待…")
            warned_qr = True
    else:
        print("\n✗ %d 秒内未回读到新摘要。**别原地重跑**：先查已发表列表/修改记录再决定。"
              % args.wait)

    if last:
        print("[终态回读] digest = %r" % (last.get("digest") or ""))
        print("            remain_modify_count = %s ｜ modify_status = %s ｜ appmsg_modified = %s"
              % (last.get("remain_modify_count"), last.get("modify_status"),
                 last.get("appmsg_modified")))
        print("            modify_wording = %r ｜ content_url = %s"
              % (last.get("modify_wording"), last.get("content_url")))
        ok = (last.get("digest") or "").strip() == text.strip()
        print("[结论] %s" % ("PASS" if ok else "UNCONFIRMED"))
        return 0 if ok else 1
    return 1


if __name__ == "__main__":
    sys.exit(main())
