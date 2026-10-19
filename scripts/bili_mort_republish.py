"""补发B站缺失的 `mort` 草稿：API 建正文 + UI 传封面（两步）。

## 为什么必须分两步

- **封面压根没有 API**：`draft/add` 的 `image_urls` 只认**远端 URL**，
  本地 PNG 塞不进去（`file://` 也不行）。
- 所以顺序只能是：① `draft/add` 新建（拿到 aid，正文完整）
  → ② 打开编辑器 → 删旧封面占位 → 上传本地 PNG → 存草稿。

⚠⚠ 反过来做（先传封面再建草稿）没意义：新建请求不带 `image_urls`，
封面一定丢。反过来先建后传，最终 `image_urls` 由 UI 上传写入，才是对的。

## 为什么不用 `save_via_api(replace=True)`

它要按标题在草稿箱找旧稿，`mort` 远端**不存在** → 直接失败。
这里是纯新建，`replace=False`。

用法：
    python -u -X utf8 scripts/bili_mort_republish.py          # 建草稿
    python -u -X utf8 scripts/bili_mort_republish.py --cover  # 传封面
    python -u -X utf8 scripts/bili_mort_republish.py --verify # 回读验真
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ["no_proxy"] = "*"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bili_title_push as tp        # noqa: E402
import bili_draft_api as api         # noqa: E402

CID = "mort"
OUT = os.path.join(tp.bp.ROOT, "out", "bili_mort_republish.json")


def _save(d):
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
        f.write("\n")
    os.replace(tmp, OUT)


def _load():
    try:
        with open(OUT, encoding="utf-8") as f:
            return json.load(f)
    except (ValueError, OSError):
        return {}


def make():
    """① API 新建草稿（正文完整）。"""
    import bilibili_publish as bp
    html = bp.load_article_html(CID)
    if not html:
        print("✗ 找不到 out/articles/%s.html" % CID)
        return 1
    title = tp.bp._bili_title_overrides().get(CID) or bp.make_title(CID, html)
    body = bp.clean_body(html)
    arg = api.build_arg(title, body)          # ⚠ 不带 image_urls/ article_id = 纯新建
    cdp = tp.connect()
    r = api.save_draft(cdp, arg)
    if not isinstance(r, dict) or r.get("code") != 0:
        print("✗ draft/add 失败：%s" % json.dumps(r, ensure_ascii=False)[:200])
        return 1
    aid = (r.get("data") or {}).get("article_id")
    time.sleep(2)
    v = api.view_draft(cdp, aid)
    paras = len(((v.get("opus") or {}).get("content") or {}).get("paragraphs") or [])
    print("✓ 新建草稿 aid=%s《%s》%d 段｜封面 %s"
          % (aid, (v.get("title") or "")[:30], paras,
             "有" if v.get("image_urls") else "无（下一步传）"))
    st = _load()
    st.update({"aid": aid, "title": (v.get("title") or "").strip(),
               "paras": paras, "made_at": time.strftime("%H:%M:%S"),
               "cover_ok": bool(v.get("image_urls"))})
    _save(st)
    return 0


def cover():
    """② UI 传封面（唯一能给本地 PNG 上传的路）。"""
    import chrome_cdp_launch as cl
    import wechat_publish as wp
    import bilibili_publish as bp
    st = _load()
    print("驱动 cover_one(%s, force=True) —— 必须与 Chrome 启动同进程" % CID)
    ok = bp.cover_one(CID, force=True)
    st["cover_ok"] = bool(ok)
    st["cover_at"] = time.strftime("%H:%M:%S")
    _save(st)
    return 0 if ok else 1


def verify():
    """③ 独立回读：标题 / 段落数 / 封面。"""
    cdp = tp.connect()
    ds = api.list_drafts(cdp, pn=1, ps=200)
    want = tp.bp._bili_title_overrides().get(CID)
    hit = [d for d in ds
           if (d.get("title") or "").strip() == want
           or tp._cid_by_title_any((d.get("title") or "").strip()) == CID]
    if not hit:
        print("✗ 草稿箱里没有 %s（现有 %d 条）" % (CID, len(ds)))
        return 1
    for d in hit:
        aid = d.get("article_id")
        v = api.view_draft(cdp, aid)
        paras = len(((v.get("opus") or {}).get("content") or {})
                    .get("paragraphs") or [])
        iu = v.get("image_urls") or []
        print("aid=%s《%s》%d 段｜封面 %s"
              % (aid, (v.get("title") or "")[:40], paras,
                 iu[0][:60] if iu else "无"))
        if paras and iu and (v.get("title") or "").strip() == want:
            print("✅ %s 完全合格" % CID)
            return 0
    print("⚠ 有草稿但不合格（正文或封面缺失）")
    return 1


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--make", action="store_true")
    g.add_argument("--cover", action="store_true")
    g.add_argument("--verify", action="store_true")
    a = ap.parse_args()
    if a.make:
        return make()
    if a.cover:
        return cover()
    return verify()


if __name__ == "__main__":
    sys.exit(main())
