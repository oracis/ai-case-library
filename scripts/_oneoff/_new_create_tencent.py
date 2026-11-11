def create_tencent(cdp, title, body_md, abstract=""):
    r"""腾讯云建草稿。返回 (draftId, 失败原因)。

    ⚠ 2026-11-01 编辑器改版：旧 ProseMirror（#cdc-rich-editor + 「切换 MD
    编辑器」）已下线，换成 CodeMirror（cm-content）。旧注入流程整体失效
    （#cdc-rich-editor 永不出现 ⇒ 原先报「等不到编辑器」假失败）。
    新流程（实测可用）：标题仍走 textarea.cdc-article-editor__title-input；
    正文走工具栏「导入MD」的文件入口 —— 用 CDP DOM.setFileInputFiles 直接喂
    清理后的 .md，绕过系统文件对话框，CodeMirror 完整载入 markdown（含 \$ 正确
    显示为字面 $，不触发数学渲染）。
    """
    cdp.open(TENCENT_NEW)
    time.sleep(3)
    _arm_hook(cdp)                      # 预埋点（含一次 reload）
    if not _wait_for(cdp,
            "!!document.querySelector('textarea.cdc-article-editor__title-input')"):
        return "", "等不到腾讯云编辑器（标题输入框不出现）"

    # 标题：真身仍是 textarea.cdc-article-editor__title-input
    r = _fill_text(cdp, "textarea.cdc-article-editor__title-input", title)
    print("    标题注入: %s" % r)
    if r == "not found":
        return "", "找不到标题输入框"

    # 正文：写清理后的 .md（去水印行），经「导入MD」文件入口灌进 CodeMirror
    clean_lines = [ln for ln in body_md.split("\n")
                   if not ln.startswith("> 平台：")]
    clean_md = "\n".join(clean_lines)
    tmp_dir = os.path.join(ROOT, "tmp")
    os.makedirs(tmp_dir, exist_ok=True)
    tmp_md = os.path.join(tmp_dir, "_tc_import_%d.md" % int(time.time()))
    with open(tmp_md, "w", encoding="utf-8") as f:
        f.write(clean_md)

    if not _click_by_text(cdp, "导入MD",
                          tags="button,[role=button],div,span,a"):
        return "", "没找到「导入MD」按钮"
    print("    已点「导入MD」", flush=True)
    time.sleep(2.5)
    try:
        doc = cdp.send("DOM.getDocument")
        root = doc["result"]["root"]["nodeId"]
        q = cdp.send("DOM.querySelector",
                     {"nodeId": root, "selector": "input[type=file]"})
        nid = (q.get("result") or {}).get("nodeId")
        if nid:
            cdp.send("DOM.setFileInputFiles",
                     {"nodeId": nid, "files": [tmp_md.replace("\\", "/")]})
            print("    导入MD 文件: %s" % tmp_md)
        else:
            print("    ⚠ 没找到 file input（导入MD 入口）")
    except Exception as e:                          # noqa: BLE001
        print("    ⚠ 导入MD 设文件失败: %s" % str(e)[:80])
    # 等 CodeMirror 载入正文
    if not _wait_for(cdp,
            "(function(){var e=document.querySelector('.cm-content');"
            "return !!(e && (e.innerText||'').length>20);})()", timeout=45):
        return "", "导入MD 后正文未载入（.cm-content 为空）"
    print("    正文载入: %s 字" % cdp.eval(
        "(document.querySelector('.cm-content')||{innerText:''}).innerText.length"))

    if not _click_by_text(cdp, "存草稿"):
        return "", "没找到「存草稿」按钮"
    print("    已点「存草稿」，等平台落库…", flush=True)
    time.sleep(10)
    print("    URL: %s" % cdp.eval("location.href"))

    did = _harvest_id(cdp)
    if not did:
        print("    未抓到建稿接口 ⇒ 走草稿箱回读兜底")
        did = _tencent_find_draft(cdp, title)
    return did, ("" if did else "未抓到建稿接口，草稿箱也没匹配到")
