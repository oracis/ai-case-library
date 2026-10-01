import os, subprocess, time, sys, json
prof = os.path.expandvars(r'%LOCALAPPDATA%\Google\ChromeCDP')
cft  = os.path.expandvars(r'%LOCALAPPDATA%\Google\ChromeForTesting\chrome-win64\chrome.exe')
lock = os.path.join(prof, 'Default', 'LOCK')
if os.path.exists(lock): os.remove(lock)

for k in ("HTTP_PROXY","HTTPS_PROXY","http_proxy","https_proxy","ALL_PROXY","all_proxy"):
    os.environ.pop(k, None)

p = subprocess.Popen(
    [cft, '--remote-debugging-port=9222', '--user-data-dir=' + prof,
     '--no-first-run', '--no-default-browser-check', '--no-sandbox'],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

import urllib.request
op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
up = False
for _ in range(40):
    time.sleep(1.5)
    try:
        op.open('http://127.0.0.1:9222/json/version', timeout=2).read()
        up = True; break
    except Exception:
        if p.poll() is not None: break
print('CDP up =', up, '| chrome alive =', p.poll() is None, flush=True)
if not up:
    print('FAIL exit=%s' % p.poll(), flush=True); raise SystemExit(1)

sys.path.insert(0, 'scripts')
import wechat_publish as wp

cdp = wp.CDP(9222)
tid, tok = cdp.connect_target, None

# 打开后台首页并读实际内容
targets = cdp.list_targets()
pages = [t for t in targets if t.get('type')=='page']
print('\n--- 现有 tab ---', flush=True)
for t in pages:
    print('  id=%s url=%s' % (t['id'][:12], (t.get('url') or '')[:90]), flush=True)

mptab = None
for t in pages:
    if 'mp.weixin.qq.com' in (t.get('url') or ''):
        mptab = t; break

if mptab is None:
    print('\n没有 mp.weixin tab，新建一个', flush=True)
    # ⚠ 必须用 new_target（Target.createTarget），不能先 send(Page.navigate)：
    # send() 要求已连接页面 target，未连接时会抛 RuntimeError。
    t = cdp.new_target('https://mp.weixin.qq.com/cgi-bin/home')
    cdp.connect_target(t['id'])
    mptab = {'id': t['id']}
    time.sleep(10)
    for t2 in cdp.list_targets():
        if 'mp.weixin.qq.com' in (t2.get('url') or ''):
            mptab = t2; break

if mptab:
    cdp.connect_target(mptab['id'])
    time.sleep(4)
    # 读页面可见文本
    raw = cdp.eval("document.body ? document.body.innerText.slice(0,400) : '(no body)'")
    print('\n--- 页面可见文本 ---', flush=True)
    print(raw, flush=True)
    print('\n--- URL ---', flush=True)
    print(cdp.eval("location.href"), flush=True)
    print('\n--- token 探测 ---', flush=True)
    print('token =', repr(wp._get_token(cdp)), flush=True)