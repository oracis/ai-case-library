# -*- coding: utf-8 -*-
"""诊断：新 Chrome 实例里首页/抽屉到底什么状态。"""
import os
import sys
import time

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _HERE)
os.environ.pop("http_proxy", None)
os.environ.pop("https_proxy", None)
os.environ["no_proxy"] = "*"
os.environ.pop("HTTP_PROXY", None)
os.environ.pop("HTTPS_PROXY", None)

from xhs_publish import PUB_PORT, HOME_URL, _JS_DRAFT_SIGNAL  # noqa: E402
from xhs_draft_fix_images import Session  # noqa: E402


def main():
    s = Session()
    s.open(fresh=True)
    url = s.js("location.href")
    title = s.js("document.title")
    print("当前页:", url, "|", title)

    # 登录态：页面上有没有头像/登录按钮
    login = s.js("""(function(){
      var t = document.body ? document.body.innerText : '';
      return JSON.stringify({
        hasLogin: t.indexOf('登录') >= 0,
        hasDraft: t.indexOf('草稿') >= 0,
        len: t.length,
        head: t.slice(0, 200)
      });})()""")
    print("页面状态:", login)

    s.js("(function(){%s\n" % "" + """
      var es=[].slice.call(document.querySelectorAll('*')).filter(function(x){
        return /图文笔记\\(\\d+\\)/.test((x.innerText||'').trim()) &&
               (x.innerText||'').trim().length<12 &&
               x.getBoundingClientRect().width>0; });
      return JSON.stringify(es.map(function(e){
        var r=e.getBoundingClientRect();
        return {tag:e.tagName, cls:String(e.className).slice(0,50),
                txt:(e.innerText||'').trim().slice(0,20),
                x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2)};}));})()""" )
    # 直接真点抽屉入口
    ok = s.open_drawer()
    print("open_drawer:", ok)
    time.sleep(2)
    sig = s.js(_JS_DRAFT_SIGNAL) or {}
    print("抽屉信号:", sig)
    cards = s.js("""(function(){
      return JSON.stringify([].slice.call(document.querySelectorAll('*'))
        .filter(function(x){return (x.innerText||'').indexOf('保存于')>=0;})
        .slice(0,3).map(function(x){return (x.innerText||'').slice(0,60);}));})()""")
    print("抽屉卡片样本:", cards)


if __name__ == "__main__":
    main()
