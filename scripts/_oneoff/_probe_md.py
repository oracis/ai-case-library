# -*- coding: utf-8 -*-
"""临时勘察：用一手 .md 判定 metrics.mrr 是否可信。产物脚本，稍后迁 scripts/_oneoff。"""
import sys, os, re, json
sys.path.insert(0, 'scripts')
import case_store as CS

MD = 'tmp/tm_md'
RE_MRR = re.compile(r'Current MRR:\s*\$?([\d,]+)')
RE_L30 = re.compile(r'Last 30 days revenue snapshot:\s*\$?([\d,]+)')
RE_ALL = re.compile(r'All-time revenue snapshot:\s*\$?([\d,]+)')
RE_SYNC = re.compile(r'Revenue data last synced:\s*([\d\-T:.Z]+)')


def parse(path):
    t = open(path, encoding='utf-8', errors='replace').read()
    d = {}
    for key, rx in (('mrr', RE_MRR), ('l30', RE_L30), ('all', RE_ALL)):
        m = rx.search(t)
        if m:
            d[key] = float(m.group(1).replace(',', ''))
    m = RE_SYNC.search(t)
    if m:
        d['sync'] = m.group(1)
    return d


cases = CS.load_cases()
print('案例', len(cases))
# 先验证单文件解析
p = parse(os.path.join(MD, 'claw-mart.md'))
print('claw-mart.md 解析:', p)

mds = {f[:-3] for f in os.listdir(MD) if f.endswith('.md')}
print('一手 .md:', len(mds))

agree, dis, onlymd = [], [], []
for c in cases:
    cid = c['id']
    if cid not in mds:
        continue
    d = parse(os.path.join(MD, cid + '.md'))
    m = c.get('metrics') or {}
    if 'mrr' not in d:
        continue
    if not isinstance(m.get('mrr'), (int, float)):
        onlymd.append((cid, d['mrr']))
        continue
    if abs(d['mrr'] - m['mrr']) < 0.5:
        agree.append(cid)
    else:
        dis.append((cid, d['mrr'], m['mrr'], d.get('sync', '')[:10]))
print('MRR 一致 %d / 不一致 %d / md有但metrics无 %d' % (len(agree), len(dis), len(onlymd)))
print()
for x in dis[:25]:
    print('  %-30s md=%-9s metrics=%-9s sync=%s' % x)
print()
print('md 有但 metrics.mrr 为空:', onlymd[:10])
