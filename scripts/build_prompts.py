#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_prompts.py —— 把「分镜表 CSV + 素材编号表 CSV」拼成 Seedance 2.5 四段式提示词包。

用法：
    python build_prompts.py --shots 第01集_分镜表.csv \
                            --assets _素材编号表.csv \
                            --out 第01集_提示词包.md \
                            --title "她的第二次人生 · 第01集"

        --shots    分镜表 CSV（列名见 assets/分镜表模板.md）
        --assets   素材编号表 CSV（列名：编号,文件,主体,类型,用途,不采用）
        --out      输出文件名；不填则打印到 stdout
        --title    提示词包标题；默认取分镜表文件名
        --ratio    画幅，默认 9:16
        --res      分辨率，默认 720P

脚本会顺带做体检并在末尾输出「校验报告」：
    · 段内时间戳重叠
    · 单句台词时长不足（字数/5 + 0.5s 缓冲）
    · 合并后总时长超过 30 秒
    · 单段超过 12 秒
    · 参考素材编号在编号表里查不到
    · 说话人写了「角色1」这类占位名
"""

import argparse
import csv
import os
import re
import sys

DEFAULT_RULE = (
    "角色外观全程保持一致；不要字幕；不要背景音乐；不要画面内文字；"
    "保持 {ratio} 竖屏；不要出现提示词以外的人物。"
)

COLUMNS = [
    "段号", "时间区间", "段时长", "段概述", "场景", "景别", "运镜",
    "画面内容", "动作细节", "台词", "说话人", "音效", "参考素材",
    "段守则", "合并到", "备注",
]

CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")

# Windows 控制台默认 GBK，中文提示会变乱码
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


# ---------------------------------------------------------------- 基础工具

def read_csv(path):
    """读 CSV，兼容 Excel 导出的 utf-8-sig / gbk。"""
    last_err = None
    for enc in ("utf-8-sig", "utf-8", "gbk", "cp936"):
        try:
            with open(path, "r", encoding=enc, newline="") as f:
                rows = list(csv.DictReader(f))
            if rows:
                return [{k.strip(): (v or "").strip() for k, v in r.items() if k} for r in rows]
        except UnicodeDecodeError as e:
            last_err = e
            continue
    raise SystemExit(f"[错误] 读不了 {path}：{last_err}")


def cell(row, name):
    return (row.get(name) or "").strip()


def parse_range(s):
    """'0s-3s' / '7.5s-10s' -> (0.0, 3.0)"""
    s = s.strip().replace("～", "-").replace("~", "-").replace("—", "-")
    m = re.match(r"^([\d.]+)\s*s?\s*-\s*([\d.]+)\s*s?$", s)
    if not m:
        return None
    return float(m.group(1)), float(m.group(2))


def fmt_t(v):
    v = round(float(v), 2)
    return f"{int(v)}" if abs(v - int(v)) < 1e-9 else f"{v:g}"


def char_count(text):
    """中文按 1 字，英文/数字按 0.5 字折算。"""
    n = 0.0
    for ch in text:
        n += 1.0 if CJK.match(ch) else 0.5
    return n


def spoken_seconds(text):
    """中文 5 字/秒 + 0.5 秒缓冲。"""
    return char_count(text) / 5.0 + 0.5


# ---------------------------------------------------------------- 素材

def load_assets(path):
    amap = {}
    for r in read_csv(path):
        code = cell(r, "编号")
        if not code:
            continue
        amap[code] = {
            "文件": cell(r, "文件"),
            "主体": cell(r, "主体"),
            "类型": cell(r, "类型"),
            "用途": cell(r, "用途"),
            "不采用": cell(r, "不采用"),
        }
    return amap


def asset_clause(code, amap, warns):
    a = amap.get(code)
    if a is None:
        warns.append(f"素材编号表里查不到 `@{code}`，请在 `_素材编号表.csv` 里补上。")
        return f"@{code}用于【编号表缺失，请补】。"

    use = a["用途"] or a["主体"]
    if code.startswith("音频"):
        s = f"@{code}作为{use}。"
    else:
        s = f"@{code}用于{use}。"
    exc = a["不采用"]
    if exc and exc not in ("无", "-", "—", "N/A"):
        s = s[:-1] + f"，{exc}。"
    return s


def split_refs(raw):
    """`@图片1;@图片6` / `图片1 图片6` 都能吃。分镜表模板里带 @，编号表里不带。"""
    parts = re.split(r"[;,；、\s]+", raw or "")
    out = []
    for p in (x.strip() for x in parts):
        p = p.lstrip("@").strip()
        if p and p not in out:
            out.append(p)
    return out


# ---------------------------------------------------------------- 组装

def build_clip(rows, clip_name, amap, ratio, warns):
    """把同一段（或合并段）的多行合成一条提示词。"""
    seg_ids = []
    for r in rows:
        sid = cell(r, "段号")
        if sid and sid not in seg_ids:
            seg_ids.append(sid)

    # --- 每个段自身的时长（优先用「段时长」列，缺失则取该段最后一个区间的结束时间）---
    seg_dur = {}
    for sid in seg_ids:
        d = 0.0
        for r in rows:
            if cell(r, "段号") != sid:
                continue
            try:
                v = float(cell(r, "段时长") or 0)
            except ValueError:
                v = 0.0
            if v > d:
                d = v
            rr = parse_range(cell(r, "时间区间"))
            if rr and rr[1] > d and v <= 0:
                d = rr[1]
        seg_dur[sid] = d

    # --- 时间轴：合并段依次累加偏移 ---
    offset = 0.0
    cur = None
    slices = []
    for r in rows:
        sid = cell(r, "段号")
        if sid != cur:
            if cur is not None:
                offset += seg_dur.get(cur, 0.0)
            cur = sid
        rng = parse_range(cell(r, "时间区间"))
        if rng is None:
            warns.append(f"{sid}：时间区间 `{cell(r,'时间区间')}` 解析不了（应为 `0s-3s` 这种写法）。")
            rng = (0.0, 3.0)
        t0, t1 = rng[0] + offset, rng[1] + offset
        slices.append((t0, t1, r, sid))

    total = max((s[1] for s in slices), default=0.0)

    # --- 概述 ---
    overview = ""
    for r in rows:
        if cell(r, "段概述"):
            overview = cell(r, "段概述")
            break
    if not overview:
        warns.append(f"{clip_name}：缺 `段概述`，请在分镜表里补一句话概述。")
        overview = "【缺段概述·请补：主体 + 地点 + 事件 + 风格 + 运镜】"

    # --- 素材指代 ---
    codes = []
    for r in rows:
        for c in split_refs(cell(r, "参考素材")):
            if c not in codes:
                codes.append(c)
    if not codes:
        warns.append(f"{clip_name}：没有任何参考素材。没有角色参考图，脸会漂。")
    else:
        n_img = len([c for c in codes if c.startswith("图片")])
        n_vid = len([c for c in codes if c.startswith("视频")])
        n_aud = len([c for c in codes if c.startswith("音频")])
        if n_img > 8:
            warns.append(
                f"{clip_name}：引用了 {n_img} 张图，超出官方稳定区（1-8 个主体）。"
                "拆分这条生成，或把次要参考图移到别段。"
            )
        if n_vid > 5:
            warns.append(f"{clip_name}：引用了 {n_vid} 段视频，超出官方稳定区（1-5 个主体）。")
        if n_aud > 10:
            warns.append(f"{clip_name}：引用了 {n_aud} 段音频，超过 10 段上限。")
        if n_img + n_vid + n_aud > 50:
            warns.append(f"{clip_name}：素材共 {n_img+n_vid+n_aud} 份，超过单次 50 份上限。")

    # --- 逐段时间戳 ---
    lines = []
    for t0, t1, r, sid in slices:
        head = f"{fmt_t(t0)}s-{fmt_t(t1)}s："
        shot = "，".join(x for x in (cell(r, "景别"), cell(r, "运镜")) if x)
        body = f"{head}{shot}。" if shot else head
        if cell(r, "画面内容"):
            body += f"{cell(r,'画面内容')}。"
        if cell(r, "动作细节"):
            body += f"{cell(r,'动作细节')}。"
        if cell(r, "台词"):
            who = cell(r, "说话人")
            if re.fullmatch(r"角色\s*\d+", who):
                warns.append(f"{sid}：说话人写成了「{who}」，必须写角色姓名，否则音色绑不上。")
            body += f"{who}说{{{cell(r,'台词')}}}。" if who else f"{{{cell(r,'台词')}}}。"
        if cell(r, "音效"):
            body += f"<{cell(r,'音效')}>"
        lines.append(body)

    # --- 守则 ---
    rule = ""
    for r in rows:
        if cell(r, "段守则"):
            rule = cell(r, "段守则")
            break
    if not rule:
        rule = DEFAULT_RULE.format(ratio=ratio)

    # --- 体检 ---
    for i in range(len(slices)):
        for j in range(i + 1, len(slices)):
            a, b = slices[i], slices[j]
            if a[0] < b[1] and b[0] < a[1]:
                warns.append(
                    f"{clip_name}：时间戳重叠 —— {fmt_t(a[0])}s-{fmt_t(a[1])}s 与 "
                    f"{fmt_t(b[0])}s-{fmt_t(b[1])}s。会导致音轨叠加、口型全乱。"
                )
    for t0, t1, r, sid in slices:
        if cell(r, "台词"):
            need = spoken_seconds(cell(r, "台词"))
            if t1 - t0 < need - 1e-9:
                warns.append(
                    f"{sid}：台词「{cell(r,'台词')}」约需 {need:.1f}s，"
                    f"但区间只有 {t1-t0:.1f}s。单句时间戳 = 字数 ÷ 5 + 0.5s 缓冲。"
                )
    if total > 30:
        warns.append(f"{clip_name}：合并后总时长 {total:.1f}s，超过 Seedance 单次 30 秒上限。")
    for sid in seg_ids:
        d = [float(cell(r, "段时长") or 0) for r in rows if cell(r, "段号") == sid]
        if d and d[0] > 12:
            warns.append(f"{sid}：段时长 {d[0]:.0f}s 偏长（>12s），模型会在长段里自己加戏，建议拆段。")

    # --- 输出 ---
    scene = next((cell(r, "场景") for r in rows if cell(r, "场景")), "")
    out = []
    out.append(f"\n---\n\n## {clip_name} · {fmt_t(total)} 秒 · {scene}\n")
    if len(seg_ids) > 1:
        out.append(f"> **合并生成**：{' + '.join(seg_ids)} → 输出文件名 `{clip_name}.mp4`\n")
    note = next((cell(r, "备注") for r in rows if cell(r, "备注")), "")
    if note:
        out.append(f"> 备注：{note}\n")

    out.append("\n```text\n【素材指代】")
    if codes:
        for c in codes:
            out.append(asset_clause(c, amap, warns))
    else:
        out.append("（无）")
    out.append("\n【概述】")
    out.append(overview)
    out.append("\n【时间戳】")
    out.extend(lines)
    out.append("\n【守则】")
    out.append(rule)
    out.append("```\n")
    out.append(f"**本段素材**：{' '.join(codes) if codes else '（无）'}")
    return "\n".join(out), codes


def main():
    ap = argparse.ArgumentParser(description="分镜表 + 素材编号表 → Seedance 2.5 提示词包")
    ap.add_argument("--shots", required=True, help="分镜表 CSV")
    ap.add_argument("--assets", required=True, help="素材编号表 CSV")
    ap.add_argument("--out", default="", help="输出 md 文件；不填则打印到 stdout")
    ap.add_argument("--title", default="", help="提示词包标题")
    ap.add_argument("--ratio", default="9:16")
    ap.add_argument("--res", default="720P")
    args = ap.parse_args()

    if not os.path.isfile(args.shots):
        raise SystemExit(f"[错误] 找不到分镜表：{args.shots}")
    if not os.path.isfile(args.assets):
        raise SystemExit(f"[错误] 找不到素材编号表：{args.assets}")

    amap = load_assets(args.assets)
    rows = read_csv(args.shots)
    if not rows:
        raise SystemExit("[错误] 分镜表是空的。")

    missing_cols = [c for c in ("段号", "时间区间") if c not in rows[0]]
    if missing_cols:
        raise SystemExit(f"[错误] 分镜表缺少必需列：{missing_cols}")

    # 按 段号 归组（保持出现顺序）
    order, groups = [], {}
    for r in rows:
        sid = cell(r, "段号")
        if not sid:
            continue
        if sid not in groups:
            groups[sid] = []
            order.append(sid)
        groups[sid].append(r)

    # 处理「合并到」：本段 + 紧随其后的段合成一条
    warns, used_codes = [], []
    runs, i = [], 0
    while i < len(order):
        run = [order[i]]
        while i < len(order) - 1:
            last_rows = groups[run[-1]]
            target = next((cell(r, "合并到") for r in last_rows if cell(r, "合并到")), "")
            if target and target == order[i + 1]:
                run.append(order[i + 1])
                i += 1
            else:
                break
        runs.append(run)
        i += 1

    title = args.title or os.path.splitext(os.path.basename(args.shots))[0]
    parts = [
        f"# {title} · Seedance 2.5 提示词包\n",
        f"> 画幅 {args.ratio} · 分辨率 {args.res} · **开启声音** · 逐段独立生成后进剪映拼接。\n",
        "> 上传顺序硬规则：**角色正面图 → 白模 → 运镜参考 → 音色参考（第 4 位）**；"
        "顺序颠倒会让音色绑到默认白模上，全片变系统默认音。\n",
        "> 音色绑定：每个角色单独上传音色并**逐角色勾选「仅当前角色可用」**。\n",
        "\n**生成顺序**：先只跑第 1 段校准脸/画风/音色 → 再跑所有有台词的段 → 最后跑过场段。\n",
    ]

    for run in runs:
        clip_name = run[0]
        merged = []
        for sid in run:
            merged.extend(groups[sid])
        block, codes = build_clip(merged, clip_name, amap, args.ratio, warns)
        parts.append(block)
        for c in codes:
            if c not in used_codes:
                used_codes.append(c)

    # --- 附录 ---
    parts.append("\n---\n\n## 附：本集素材清单\n")
    if used_codes:
        parts.append("| 编号 | 主体 | 类型 | 文件 |")
        parts.append("|---|---|---|---|")
        for c in used_codes:
            a = amap.get(c, {})
            parts.append(f"| {c} | {a.get('主体','?')} | {a.get('类型','?')} | {a.get('文件','?')} |")
    else:
        parts.append("（无）")

    parts.append("\n## 附：校验报告\n")
    if warns:
        for w in warns:
            parts.append(f"- ⚠️ {w}")
    else:
        parts.append("- ✅ 无问题：时间戳无重叠、台词时长够、总时长在上限内、素材编号全部命中。")

    parts.append("\n## 附：生成后必做")
    for b in (
        "逐段播放，检查口型与台词是否对齐",
        "1.2 倍速全片播一遍，找出细微偏差",
        "跨段验收：把每个角色的脸截帧拼图对比",
        "光线方向、道具位置、人物站位在段间接得上",
        "音色不是系统默认音、多角色没串",
        "画面里没有多余字幕和 BGM",
    ):
        parts.append(f"- [ ] {b}")

    text = "\n".join(parts)
    text = re.sub(r"[ \t]+\n", "\n", text)      # 行尾空白
    text = re.sub(r"\n{3,}", "\n\n", text)      # 多余空行
    text = text.strip() + "\n"

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"[OK] 已写出 {args.out}")
        print(f"[OK] 共 {len(runs)} 条生成（来自 {len(order)} 个段），素材引用 {len(used_codes)} 项。")
        if warns:
            print(f"[!] 校验发现 {len(warns)} 个问题：")
            for w in warns:
                print(f"    - {w}")
        else:
            print("[OK] 校验通过。")
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
