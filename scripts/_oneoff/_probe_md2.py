# -*- coding: utf-8 -*-
"""临时勘察 2：把 101 条 md/metrics 分歧按「正文数字站在谁那边」分类。"""
import sys, os, re, json
sys.path.insert(0, 'scripts')
import case_store as CS

MD = 'tmp/tm_md'
RE = {
    'mrr': re.compile(r'Current MRR:\s*\$?([\d,]+)'),
    'l30': re.compile(r'Last 30 days revenue snapshot:\s*\$?([\d,]+)'),
    'all': re.compile(r'All-time revenue snapshot:\s*\$?([\d,]+)'),
    'sync': re.compile(r'Revenue data last synced:\s*([\d\-T:.Z]+)'),
    'subs': re.compile(r'Current active subscriptions:\s*([\d,]+)'),
}
TEXT_FIELDS = ['one_liner', 'what_it_does', 'how_it_makes_money', 'why_it_works',
               'china_fit', 'solo_fit', 'what_to_copy', 'moat', 'playbook']


def walk_text(c):
    out = []
    for f in TEXT_FIELDS:
        v = c.get(f)
        if isinstance(v, str):
            out.append((f, v))
        elif isinstance(v, list):
            for i, x in enumerate(v):
                if isinstance(x, str):
                    out.append(('%s[%d]' % (f, i), x))
        elif isinstance(v, dict):
            for k, x in v.items():
                if isinstance(x, str):
                    out.append(('%s.%s' % (f, k), x))
    return out


def parse(path):
    t = open(path, encoding='utf-8', errors='replace').read()
    d = {}
    for k, rx in RE.items():
        m = rx.search(t)
        if m:
            d[k] = m.group(1) if k == 'sync' else float(m.group(1).replace(',', ''))
    return d


cases = CS.load_cases()
mds = {f[:-3] for f in os.listdir(MD) if f.endswith('.md')}

A, B, C, D = [], [], [], []   # A=正文站md侧 B=正文站metrics侧 C=第三种 D=无md
for c in cases:
    cid = c['id']
    m = c.get('metrics') or {}
    mrr = m.get('mrr')
    texts = walk_text(c)
    nums = set()
    for _, t in texts:
        for mt in re.finditer(r'MRR[^0-9\$]{0,10}\$?\s?([\d][\d,]*)', t):
            nums.add(round(float(mt.group(1).replace(',', '')), 2))
    if not nums:
        continue
    if cid not in mds:
        D.append((cid, mrr, sorted(nums), c.get('verified_at')))
        continue
    d = parse(os.path.join(MD, cid + '.md'))
    if 'mrr' not in d:
        continue
    mdv = d['mrr']
    if isinstance(mrr, (int, float)):
        if abs(mdv - mrr) < 0.5:
            others = [n for n in nums if abs(n - mdv) > 0.5]
            if others:
                B.append((cid, mdv, mrr, sorted(others), c.get('verified_at'), d.get('sync', '')[:10]))
        else:
            at_md = any(abs(n - mdv) < 0.5 for n in nums)
            at_m = any(abs(n - mrr) < 0.5 for n in nums)
            if at_md and not at_m:
                A.append((cid, mdv, mrr, c.get('verified_at'), d.get('sync', '')[:10]))
            elif at_m and not at_md:
                C.append(('正文站metrics', cid, mdv, mrr, c.get('verified_at'), d.get('sync', '')[:10]))
            else:
                C.append(('正文两边都不站', cid, mdv, mrr, c.get('verified_at'), d.get('sync', '')[:10]))

print('=== 分类 ===')
print('A 正文=md值、metrics不同  :', len(A))
print('B 正文第三种数字(md=metrics):', len(B))
print('C 其他(正文站metrics/两边不站):', len(C))
print('D 无一手 .md 无法判定      :', len(D))
print()
print('--- A 类：md vs metrics 与 verified_at/sync ---')
for x in A[:20]:
    print('  %-28s md=%-9s metrics=%-9s ver=%-11s sync=%s' % x)
print()
print('--- C 类 ---')
for x in C[:12]:
    print('  %-16s %-28s md=%-9s metrics=%-9s ver=%-11s sync=%s' % x)
print()
print('--- B 类 ---')
for x in B[:12]:
    print('  %-28s md=metrics=%-9s 正文=%-14s ver=%s sync=%s' % x)
print()
print('--- D 类（无 .md 但正文与 metrics 冲突）样本 ---')
for x in D[:12]:
    print('  %-28s metrics=%-9s 正文=%-18s ver=%s' % x)
