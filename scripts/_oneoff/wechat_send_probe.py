#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性探测（只读）：公众号草稿编辑页的「发表」入口长什么样。

不点任何写操作。目标：
  1. 草稿箱卡片怎么拿到 appmsgid；
  2. 打开某篇草稿的编辑页，读出顶栏按钮（「发表」/「群发」）与它们的选择器；
  3. 看页面里有没有「群发通知」之类的开关（订阅号每天 1 次的限制就在这）。
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import wechat_publish as wp

TOKEN = sys.argv[1] if len(sys.argv) > 1 else "827333358"
HOME = ("https://mp.weixin.qq.com/cgi-bin/home?t=home/index&lang=zh_CN"
        "&token=" + TOKEN)
EDIT = ("https://mp.weixin.qq.com/cgi-bin/appmsg?t=media/appmsg_edit&action=edit"
        "&type=77&appmsgid=%s&token=" + TOKEN + "&lang=zh_CN")


def main():
    cdp = wp.CDP(9222)
    tid = cdp.new_target(HOME)["id"]
    cdp.connect_target(tid)
    time.sleep(8)
    cdp.eval("""(function(){
      var s=[].slice.call(document.querySelectorAll('span.weui-desktop-menu__name'))
        .filter(function(e){return (e.innerText||'').trim()==='草稿箱';})[0];
      if(!s) return 0; (s.closest('li')||s).click(); s.click(); return 1;
    })()""")
    time.sleep(8)

    # 草稿箱卡片：从 DOM 里捞 appmsgid（卡片通常带 data-appmsgid 或链接参数）
    got = cdp.eval("""(function(){
      var out=[];
      [].slice.call(document.querySelectorAll('*')).forEach(function(e){
        ['data-appmsgid','data-id'].forEach(function(k){
          var v=e.getAttribute&&e.getAttribute(k);
          if(v && /^\\d{6,}$/.test(v) && out.indexOf(v)<0) out.push(v);
        });
        var h=e.getAttribute&&e.getAttribute('href');
        if(h && h.indexOf('appmsgid=')>=0){
          var m=h.match(/appmsgid=(\\d+)/); if(m && out.indexOf(m[1])<0) out.push(m[1]);
        }
      });
      return out.slice(0,6);
    })()""")
    print("appmsgid 候选：", got)

    if not got:
        print("（DOM 里没找到 appmsgid，改从 JSON 接口读）")
        got = cdp.eval("""(async function(){
          var r=await fetch('/cgi-bin/appmsg?action=list_ex&type=77&begin=0'
            +'&count=5&token=%s&lang=zh_CN&f=json',{credentials:'include'});
          var j=await r.json();
          var l=(j.app_msg_list||[]).map(function(x){return String(x.appmsgid)});
          return l;
        })()""" % TOKEN)
        print("接口返回：", got)

    if not got:
        return

    aid = got[0]
    print("打开编辑页 appmsgid=%s" % aid)
    t2 = cdp.new_target(EDIT % aid)["id"]
    cdp.connect_target(t2)
    time.sleep(10)
    info = cdp.eval("""(function(){
      var t=(document.body.innerText||'').replace(/\\s+/g,' ');
      var acts={};
      [].slice.call(document.querySelectorAll('a,button,span,div'))
        .forEach(function(e){
          if(e.children.length) return;
          var s=(e.innerText||'').trim();
          if(!s||s.length>8) return;
          if(['发表','群发','保存','预览','定时发表','存为草稿','发送'].indexOf(s)>=0){
            acts[s]=(acts[s]||0)+1;
          }
        });
      return {url:location.href.slice(0,120), acts:acts, head:t.slice(0,200)};
    })()""")
    print(json.dumps(info, ensure_ascii=False, indent=1)[:1200])


if __name__ == "__main__":
    main()
