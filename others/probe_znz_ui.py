"""
probe_znz_ui.py
用途: 在你的 Windows 机器上跑,枚举指南针客户端的 UI Automation 控件树,
      输出到 probe_znz_ui_output.txt,用来定位"代码输入框"、"K 线主图"、
      "导出菜单"等关键控件。

运行步骤(在你的 Windows 上):
    1) pip install pywinauto
    2) 启动指南针客户端,登录,调出 0AMV 日 K 线图
    3) python probe_znz_ui.py
    4) 把 probe_znz_ui_output.txt 发给我
"""

import sys
from pathlib import Path
from pywinauto import Application


def find_znz_window():
    """尝试多种标题匹配,找到指南针主窗口。"""
    title_patterns = [
        r".*指南针.*",
        r".*Compass.*",
        r".*znz.*",
        r".*ZNZ.*",
    ]
    for pat in title_patterns:
        try:
            app = Application(backend="uia").connect(title_re=pat, timeout=5)
            return app, pat
        except Exception:
            continue
    raise RuntimeError(
        "找不到指南针客户端窗口。请确认:\n"
        "  - 客户端已启动\n"
        "  - 客户端没有最小化到托盘\n"
        "  - 窗口没有被其他程序遮挡\n"
        "提示: 试试先手动点一下指南针窗口让它获得焦点再跑。"
    )


def info_str(ctrl):
    """统一从 element_info 取属性,跨 backend 兼容。"""
    try:
        ei = ctrl.element_info
        ct = ei.control_type or "?"
        nm = ei.name or ""
        aid = ei.automation_id or ""
        rect = ei.rectangle
        return ct, nm, aid, rect
    except Exception as e:
        return "?", "", "", None


def dump_tree(ctrl, depth=0, lines=None):
    """递归枚举控件树,带深度限制避免无限嵌套。"""
    if lines is None:
        lines = []
    if depth > 8:
        lines.append(f"{'  '*depth}<truncated, depth>8>")
        return lines
    try:
        ct, nm, aid, rect = info_str(ctrl)
        info = (
            f"{'  '*depth}[{ct}] "
            f"name={nm!r} "
            f"auto_id={aid!r} "
            f"rect={rect}"
        )
        lines.append(info)
    except Exception as e:
        lines.append(f"{'  '*depth}<error reading ctrl: {e}>")
    try:
        for child in ctrl.children():
            dump_tree(child, depth + 1, lines)
    except Exception:
        pass
    return lines


def dump_edits(win):
    """单独列出所有 Edit 类控件(代码输入框候选)。"""
    lines = ["\n\n===== 所有 Edit 类控件(代码输入框候选) ====="]
    try:
        edits = win.descendants(control_type="Edit")
    except Exception as e:
        lines.append(f"枚举失败: {e}")
        return lines
    if not edits:
        lines.append("(无 — 客户端可能用 ComboBox 或自定义控件当代码输入)")
    for i, e in enumerate(edits, 1):
        try:
            _, nm, aid, rect = info_str(e)
            lines.append(
                f"  #{i} name={nm!r} "
                f"auto_id={aid!r} "
                f"rect={rect}"
            )
        except Exception as ex:
            lines.append(f"  #{i} <read failed: {ex}>")
    return lines


def dump_panes(win):
    """列出所有 Pane 类控件(主图 / 副图候选)。"""
    lines = ["\n\n===== 所有 Pane 类控件(K 线图区域候选) ====="]
    try:
        panes = win.descendants(control_type="Pane")
    except Exception as e:
        lines.append(f"枚举失败: {e}")
        return lines
    panes_with_area = []
    for p in panes:
        try:
            _, _, _, rect = info_str(p)
            if rect is None:
                continue
            w = rect.width()
            h = rect.height()
            panes_with_area.append((w * h, p))
        except Exception:
            continue
    panes_with_area.sort(key=lambda x: -x[0])
    for i, (area, p) in enumerate(panes_with_area[:20], 1):
        try:
            _, nm, aid, rect = info_str(p)
            lines.append(
                f"  #{i} area={area} "
                f"name={nm!r} "
                f"auto_id={aid!r} "
                f"rect={rect}"
            )
        except Exception as ex:
            lines.append(f"  #{i} <read failed: {ex}>")
    return lines


def dump_menus(win):
    """列出顶层菜单(MenuBar)的所有 MenuItem。"""
    lines = ["\n\n===== 顶层菜单栏(MenuBar 项) ====="]
    try:
        menubars = win.descendants(control_type="MenuBar")
    except Exception as e:
        lines.append(f"枚举失败: {e}")
        return lines
    if not menubars:
        lines.append("(无 MenuBar,客户端可能用自定义工具栏代替)")
    for mb in menubars:
        try:
            for item in mb.children():
                _, nm, aid, _ = info_str(item)
                lines.append(f"  name={nm!r} auto_id={aid!r}")
        except Exception as ex:
            lines.append(f"  <read failed: {ex}>")
    return lines


def dump_buttons(win):
    """列出顶层工具栏的所有 Button(找"导出"按钮候选)。"""
    lines = ["\n\n===== 顶层 ToolBar / Button(导出按钮候选) ====="]
    try:
        buttons = win.descendants(control_type="Button")
    except Exception as e:
        lines.append(f"枚举失败: {e}")
        return lines
    keywords = ("导出", "输出", "数据", "Export", "ExportData", "保存", "Save")
    found = False
    for b in buttons:
        try:
            _, nm, _, _ = info_str(b)
            if any(k in (nm or "") for k in keywords):
                found = True
                _, _, aid, rect = info_str(b)
                lines.append(
                    f"  ★ name={nm!r} "
                    f"auto_id={aid!r} "
                    f"rect={rect}"
                )
        except Exception:
            continue
    if not found:
        lines.append("(无明显导出按钮,客户端可能用右键菜单)")
    return lines


def dump_all_controls_by_type(win):
    """统计所有控件类型,看看指南针用了哪些 UIA control_type。"""
    lines = ["\n\n===== 所有控件类型统计 ====="]
    try:
        all_ctrls = win.descendants()
        type_count = {}
        for c in all_ctrls:
            try:
                ei = c.element_info
                ct = ei.control_type or "?"
                type_count[ct] = type_count.get(ct, 0) + 1
            except Exception:
                continue
        for ct, cnt in sorted(type_count.items(), key=lambda x: -x[1]):
            lines.append(f"  {ct}: {cnt}")
        lines.append(f"  TOTAL: {sum(type_count.values())}")
    except Exception as e:
        lines.append(f"枚举失败: {e}")
    return lines


def dump_hyperlinks(win):
    """dump 所有 Hyperlink 控件 — 指南针 81 个 Hyperlink 里可能藏着导出入口。"""
    lines = ["\n\n===== 所有 Hyperlink(导出/数据入口候选) ====="]
    try:
        links = win.descendants(control_type="Hyperlink")
    except Exception as e:
        lines.append(f"枚举失败: {e}")
        return lines
    keywords = ("导出", "输出", "数据", "保存", "Export", "Save", "下载", "Excel", "CSV")
    for i, l in enumerate(links[:60], 1):
        try:
            _, nm, aid, rect = info_str(l)
            star = "★" if any(k in (nm or "") for k in keywords) else " "
            lines.append(f"  {star} #{i} name={nm!r} auto_id={aid!r} rect={rect}")
        except Exception:
            continue
    return lines


def dump_top_menu_pane(win):
    """顶部'菜单栏' Pane 的子树 — 通常蓝色工具栏按钮是 Pane/Button,不是 MenuItem。"""
    lines = ["\n\n===== 顶部菜单栏 Pane 的子项 ====="]
    try:
        panes = win.descendants(control_type="Pane")
    except Exception as e:
        lines.append(f"枚举失败: {e}")
        return lines
    menu_panes = [p for p in panes if info_str(p)[1] == "菜单栏"]
    if not menu_panes:
        lines.append("(没找到名为'菜单栏'的 Pane)")
        return lines
    for mp in menu_panes:
        lines.append(f"--- Pane: 菜单栏 rect={info_str(mp)[3]} ---")
        try:
            children = mp.children()
            for c in children:
                ct, nm, aid, rect = info_str(c)
                lines.append(f"  [{ct}] name={nm!r} auto_id={aid!r} rect={rect}")
                # 递归一层,看按钮下的子项
                try:
                    for gc in c.children():
                        gct, gnm, gaid, grect = info_str(gc)
                        lines.append(f"    [{gct}] name={gnm!r} auto_id={gaid!r} rect={grect}")
                except Exception:
                    pass
        except Exception as e:
            lines.append(f"枚举失败: {e}")
    return lines


def probe_right_click_on_chart(win):
    """在 K 线主图上右键,探测弹出的菜单项。"""
    lines = ["\n\n===== K线主图右键菜单探测 ====="]
    try:
        chart_window = win.child_window(auto_id="65281", control_type="Window")
        chart_pane = chart_window.child_window(auto_id="59648", control_type="Pane")
    except Exception as e:
        lines.append(f"找不到主图控件: {e}")
        return lines

    lines.append(f"[info] 在主图 Pane 上发送右键 (rect={info_str(chart_pane)[3]})")
    try:
        chart_pane.right_click()
    except Exception as e:
        lines.append(f"right_click failed: {e}")
        return lines

    import time
    time.sleep(2)  # 等菜单弹出

    # 弹出菜单通常是 control_type="Menu" 顶层出现
    try:
        menus = win.descendants(control_type="Menu")
    except Exception as e:
        lines.append(f"枚举 Menu 失败: {e}")
        return lines

    if not menus:
        lines.append("(没找到 Menu 控件 — 客户端可能用自绘菜单,不会出现在 UIA 树里)")
        return lines

    for m in menus:
        lines.append(f"--- Menu rect={info_str(m)[3]} ---")
        try:
            for item in m.children():
                ct, nm, aid, rect = info_str(item)
                lines.append(f"  [{ct}] name={nm!r} auto_id={aid!r} rect={rect}")
                try:
                    for sub in item.children():
                        sct, snm, said, srect = info_str(sub)
                        lines.append(f"    [{sct}] name={snm!r} auto_id={said!r} rect={srect}")
                except Exception:
                    pass
        except Exception as e:
            lines.append(f"枚举子项失败: {e}")

    # 按 ESC 关掉菜单
    try:
        from pywinauto.keyboard import send_keys
        send_keys("{ESC}")
    except Exception:
        pass
    return lines


def main():
    out_path = Path("probe_znz_ui_output.txt")

    try:
        app, pattern = find_znz_window()
    except Exception as e:
        msg = f"启动失败: {e}\n\n"
        msg += "如果客户端已开,请检查:\n"
        msg += "  1. 窗口标题里是否含'指南针'或'Compass'\n"
        msg += "  2. 客户端是否最小化(最小化也算 connected,但截图看不到)\n"
        msg += "  3. 是否有多个 pywinauto 进程冲突\n"
        Path(out_path).write_text(msg, encoding="utf-8")
        print(msg)
        return 1

    win = app.window(title_re=pattern)
    win_title = win.window_text()
    try:
        win_rect_before = win.element_info.rectangle
    except Exception:
        win_rect_before = "?"

    # 强制把窗口从最小化/隐藏状态恢复 — 否则只能枚举到 8 个壳控件
    print(f"[info] window rect BEFORE restore: {win_rect_before}")
    try:
        win.restore()  # 如果是最小化,会还原
        print("[info] win.restore() called")
    except Exception as e:
        print(f"[warn] restore failed: {e}")
    try:
        win.set_focus()
        print("[info] set_focus called")
    except Exception as e:
        print(f"[warn] set_focus failed: {e}")

    import time
    time.sleep(2)  # 给 UI 一点时间刷新

    try:
        win_rect_after = win.element_info.rectangle
    except Exception:
        win_rect_after = "?"
    print(f"[info] window rect AFTER restore: {win_rect_after}")
    print(f"[ok] output: {out_path.resolve()}")

    lines = []
    lines.append(f"# probe_znz_ui output")
    lines.append(f"# window title: {win_title}")
    lines.append(f"# window rect BEFORE restore: {win_rect_before}")
    lines.append(f"# window rect AFTER restore: {win_rect_after}")
    lines.append("")
    lines.append("===== full control tree =====")
    lines.extend(dump_tree(win))
    lines.extend(dump_all_controls_by_type(win))
    lines.extend(dump_edits(win))
    lines.extend(dump_panes(win))
    lines.extend(dump_menus(win))
    lines.extend(dump_buttons(win))
    lines.extend(dump_hyperlinks(win))
    lines.extend(dump_top_menu_pane(win))
    lines.extend(probe_right_click_on_chart(win))

    text = "\n".join(lines)
    out_path.write_text(text, encoding="utf-8")
    print(f"[done] 写出 {len(lines)} 行")
    return 0


if __name__ == "__main__":
    sys.exit(main())
