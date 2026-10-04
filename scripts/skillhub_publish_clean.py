# -*- coding: utf-8 -*-
"""SkillHub / ClawHub 发布中转：绕开平台禁止的文件类型。

平台只校验包内文件类型，dry-run 不查这一层，所以「预检通过」≠「能发上去」。
实测被拒类型： .gitignore（400 不允许的文件类型: .gitignore）、
                *.png / *.jpg（400 不允许的文件类型: _probe_image.png）。
                .git 整目录同样要排掉。

正解 = 复制到临时目录 → 剔掉禁发文件 → 从副本发布 → 源目录保持不动。
（不能用「把图片挪走」的方式，那会破坏 skill 自身的图标/文档引用。）

用法：
    python scripts/skillhub_publish_clean.py <slug> [--changelog "..."] [--dry-run]
    python scripts/skillhub_publish_clean.py <slug> --market clawhub \
        --categories development,productivity
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile

SKILLS_ROOT = r"C:\Users\DELL\.workbuddy\skills"
SH_CLI = r"C:\Users\DELL\.skillhub\skills_store_cli.py"
CH_NODE = r"C:\Users\DELL\.workbuddy\binaries\node\versions\22.22.2-6\node.exe"
CH_CLI = r"C:\Users\DELL\.workbuddy\binaries\node\workspace\node_modules\clawhub\bin\clawdhub.js"

# 平台禁止打包的文件：目录名（任意层级）与扩展名
SKIP_NAMES = {".git", ".gitignore", ".gitattributes", ".DS_Store",
              "__pycache__", ".pytest_cache", "node_modules"}
SKIP_EXT = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".bmp", ".webp",
            ".pyc", ".zip", ".tar", ".gz"}


def stage_copy(slug, keep_ext=None):
    """把 skill 复制到临时目录并剔除禁发文件，返回副本路径。"""
    src = os.path.join(SKILLS_ROOT, slug)
    if not os.path.isfile(os.path.join(src, "SKILL.md")):
        sys.exit("找不到 SKILL.md: %s" % src)
    stage = tempfile.mkdtemp(prefix="shpub_")
    dst = os.path.join(stage, slug)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(*SKIP_NAMES))
    exts = SKIP_EXT if keep_ext is None else (SKIP_EXT - set(keep_ext))
    removed = []
    for root, _dirs, files in os.walk(dst):
        for f in files:
            if os.path.splitext(f)[1].lower() in exts:
                os.remove(os.path.join(root, f))
                removed.append(f)
    return dst, removed


def publish(slug, changelog=None, dry_run=False, keep_ext=None,
            market="skillhub", categories=None, version=None):
    dst, removed = stage_copy(slug, keep_ext)
    if market == "clawhub":
        cmd = [CH_NODE, CH_CLI, "publish", dst, "--slug", slug]
        if categories:
            cmd += ["--categories", ",".join(categories)]
        if version:
            # 必须显式传版本：ClawHub 自动推断的"下一版"可能低于本地实际版本
            # （wechat-file-organizer 本地 2.1.0，自动推断却给 0.1.3）。
            cmd += ["--version", version]
    else:
        cmd = [sys.executable, SH_CLI, "publish", dst]
    if changelog:
        cmd += ["--changelog", changelog]
    if dry_run:
        cmd.append("--dry-run")
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        out = (p.stdout or "") + (p.stderr or "")
        print("[%s/%s] 剔除禁发文件 %d 个: %s"
              % (market, slug, len(removed), ", ".join(removed) or "-"))
        print(out.strip())
        return p.returncode, out
    finally:
        shutil.rmtree(os.path.dirname(dst), ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("slug")
    ap.add_argument("--changelog")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--market", default="skillhub",
                    choices=["skillhub", "clawhub"])
    ap.add_argument("--categories", action="append",
                    help="ClawHub 分类 slug，可重复")
    ap.add_argument("--keep-ext", action="append",
                    help="保留该扩展名不被剔除，可重复")
    ap.add_argument("--version", help="显式指定版本号（ClawHub 强烈建议）")
    a = ap.parse_args()
    rc, _ = publish(a.slug, a.changelog, a.dry_run, a.keep_ext,
                    a.market, a.categories, a.version)
    sys.exit(rc)


if __name__ == "__main__":
    main()
