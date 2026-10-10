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
        --cps      台词语速（字/秒），默认 5；真人风建议 4–4.5

两层编号：
    编号表里的「图片6」是全局资产 ID，管理用，永不变更；
    提示词里的「@图片2」是这一条生成的上传槽位，按上传顺序从 1 编号。
    脚本自动把全局 ID 换算成槽位号，并给每条生成输出一份「上传清单」。
    上传顺序固定为：图片 → 视频 → 音频（音色永远排在所有画面素材之后）。

脚本会顺带做体检并在末尾输出「校验报告」：
    · 段内时间戳重叠                · 单句台词时长不足（字数 ÷ 语速 + 0.5s 缓冲）
    · 合并后总时长超过 30 秒        · 单段超过 12 秒
    · 参考素材编号在编号表里查不到  · 说话人写成「角色1 / 人物2」这类占位名
    · 整条没有参考素材              · 参考图 / 视频 / 音频超过上限
    · 时间区间、段时长写法解析不了  · 有台词的说话人没挂对应音色
    · 台词没写说话人                · 同一条生成里混了两种语言
    · 提示词超过 1500 字
"""

import argparse
import csv
import os
import re
import sys

DEFAULT_RULE = (
    "角色外观全程保持一致；不要字幕；不要背景音乐；不要画面内文字；"
    "保持 {ratio} {orient}构图；不要出现提示词以外的人物。"
)

COLUMNS = [
    "段号", "时间区间", "段时长", "段概述", "场景", "景别", "运镜",
    "画面内容", "动作细节", "台词", "说话人", "音效", "参考素材",
    "段守则", "合并到", "备注",
]

# 这些列里允许直接写 @图片N，脚本会一起换算成槽位号
TEXT_COLS = ("段概述", "画面内容", "动作细节", "段守则")

KINDS = ("图片", "视频", "音频")
EXT_KIND = {
    ".png": "图片", ".jpg": "图片", ".jpeg": "图片", ".webp": "图片",
    ".mp4": "视频", ".mov": "视频", ".webm": "视频",
    ".wav": "音频", ".mp3": "音频", ".flac": "音频", ".m4a": "音频",
}

CJK = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
KANA = re.compile(r"[぀-ヿ]")
HANGUL = re.compile(r"[가-힯]")
LATIN = re.compile(r"[A-Za-z]")
PLACEHOLDER = re.compile(r"^(角色|人物)\s*\d+$")
INLINE_REF = re.compile(r"@((?:图片|视频|音频)\d+)")

PROMPT_MAX = 1500
PROMPT_MIN = 500

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


def num(s):
    """'10' / '10秒' / '10s' / ' 7.5 ' -> float；解析不了返回 None。"""
    m = re.search(r"\d+(?:\.\d+)?", s or "")
    return float(m.group(0)) if m else None


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


def spoken_seconds(text, cps):
    """字数 ÷ 语速 + 0.5 秒缓冲。"""
    return char_count(text) / cps + 0.5


def language_of(text):
    if KANA.search(text):
        return "日语"
    if HANGUL.search(text):
        return "韩语"
    if CJK.search(text):
        return "中文"
    if LATIN.search(text):
        return "拉丁字母语言（英语等）"
    return ""


def orientation(ratio):
    m = re.match(r"^\s*([\d.]+)\s*[:：xX×]\s*([\d.]+)\s*$", ratio or "")
    if not m:
        return ""
    w, h = float(m.group(1)), float(m.group(2))
    return "竖屏" if h > w else ("横屏" if w > h else "方形")


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


def kind_of(code, amap):
    """图片 / 视频 / 音频。先看编号前缀，再看文件扩展名，都没有就当图片。"""
    for k in KINDS:
        if code.startswith(k):
            return k
    ext = os.path.splitext(amap.get(code, {}).get("文件", ""))[1].lower()
    return EXT_KIND.get(ext, "图片")


def asset_clause(code, slot, amap):
    a = amap.get(code)
    if a is None:
        return f"@{slot}用于【编号表缺失，请补】。"
    use = a["用途"] or a["主体"]
    s = f"@{slot}作为{use}。" if slot.startswith("音频") else f"@{slot}用于{use}。"
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


def assign_slots(codes, amap):
    """全局编号 -> 上传槽位。顺序：图片 → 视频 → 音频，同类按首次出现顺序。"""
    ordered, slots = [], {}
    for k in KINDS:
        n = 0
        for c in codes:
            if kind_of(c, amap) == k:
                n += 1
                slots[c] = f"{k}{n}"
                ordered.append(c)
    return ordered, slots


def remap_inline(text, slots):
    return INLINE_REF.sub(lambda m: "@" + slots.get(m.group(1), m.group(1)), text)


# ---------------------------------------------------------------- 组装

def build_clip(rows, clip_name, amap, ratio, cps, warns, tips):
    """把同一段（或合并段）的多行合成一条提示词。"""
    seg_ids = []
    for r in rows:
        sid = cell(r, "段号")
        if sid and sid not in seg_ids:
            seg_ids.append(sid)

    # --- 每个段自身的时长（优先用「段时长」列，缺失则取该段最后一个区间的结束时间）---
    seg_dur, seg_dur_given = {}, {}
    for sid in seg_ids:
        given, last_end = 0.0, 0.0
        for r in rows:
            if cell(r, "段号") != sid:
                continue
            raw = cell(r, "段时长")
            if raw:
                v = num(raw)
                if v is None:
                    warns.append(f"{sid}：段时长 `{raw}` 解析不了，应填数字（如 `10`）。")
                elif v > given:
                    given = v
            rr = parse_range(cell(r, "时间区间"))
            if rr and rr[1] > last_end:
                last_end = rr[1]
        seg_dur_given[sid] = given
        seg_dur[sid] = given if given > 0 else last_end

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

    # --- 素材：参考素材列 + 文本列里直接写的 @编号 ---
    codes = []
    for r in rows:
        for c in split_refs(cell(r, "参考素材")):
            if c not in codes:
                codes.append(c)
        for col in TEXT_COLS:
            for c in INLINE_REF.findall(cell(r, col)):
                if c not in codes:
                    codes.append(c)
    for c in codes:
        if c not in amap:
            warns.append(f"素材编号表里查不到 `@{c}`，请在 `_素材编号表.csv` 里补上。")
    ordered, slots = assign_slots(codes, amap)

    if not codes:
        warns.append(f"{clip_name}：没有任何参考素材。没有角色参考图，脸会漂。")
    else:
        n = {k: len([c for c in codes if kind_of(c, amap) == k]) for k in KINDS}
        if n["图片"] > 8:
            warns.append(
                f"{clip_name}：引用了 {n['图片']} 张图，超出官方稳定区（1-8 个主体）。"
                "拆分这条生成，或把次要参考图移到别段。"
            )
        if n["视频"] > 5:
            warns.append(f"{clip_name}：引用了 {n['视频']} 段视频，超出官方稳定区（1-5 个主体）。")
        if n["音频"] > 10:
            warns.append(f"{clip_name}：引用了 {n['音频']} 段音频，超过 10 段上限。")
        if sum(n.values()) > 50:
            warns.append(f"{clip_name}：素材共 {sum(n.values())} 份，超过单次 50 份上限。")

    # --- 概述：合并段每段的概述都保留 ---
    overviews = []
    for sid in seg_ids:
        o = next((cell(r, "段概述") for r in rows if cell(r, "段号") == sid and cell(r, "段概述")), "")
        if o and o not in overviews:
            overviews.append(o)
    if not overviews:
        warns.append(f"{clip_name}：缺 `段概述`，请在分镜表里补一句话概述。")
        overview = "【缺段概述·请补：主体 + 地点 + 事件 + 风格 + 运镜】"
    elif len(overviews) == 1:
        overview = overviews[0]
    else:
        overview = "随后，".join(o.rstrip("。") + "。" for o in overviews)
    overview = remap_inline(overview, slots)

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
            if PLACEHOLDER.fullmatch(who):
                warns.append(f"{sid}：说话人写成了「{who}」，必须写角色姓名，否则音色绑不上。")
            elif not who:
                warns.append(f"{sid}：台词「{cell(r,'台词')}」没写说话人，音色绑不上。")
            body += f"{who}说{{{cell(r,'台词')}}}。" if who else f"{{{cell(r,'台词')}}}。"
        if cell(r, "音效"):
            body += f"<{cell(r,'音效')}>"
        lines.append(remap_inline(body, slots))

    # --- 守则：合并段的守则去重拼接 ---
    rules = []
    for r in rows:
        x = cell(r, "段守则")
        if x and x not in rules:
            rules.append(x)
    rule = "；".join(x.rstrip("。；;") for x in rules) + "。" if rules else \
        DEFAULT_RULE.format(ratio=ratio, orient=orientation(ratio))
    rule = remap_inline(rule, slots)

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
            need = spoken_seconds(cell(r, "台词"), cps)
            if t1 - t0 < need - 1e-9:
                warns.append(
                    f"{sid}：台词「{cell(r,'台词')}」约需 {need:.1f}s，"
                    f"但区间只有 {t1-t0:.1f}s。单句时间戳 = 字数 ÷ {cps:g} + 0.5s 缓冲。"
                )
    if total > 30:
        warns.append(f"{clip_name}：合并后总时长 {total:.1f}s，超过 Seedance 单次 30 秒上限。")
    for sid in seg_ids:
        d = seg_dur.get(sid, 0.0)
        if d > 12:
            warns.append(f"{sid}：段时长 {d:g}s 偏长（>12s），模型会在长段里自己加戏，建议拆段。")

    # 说话人必须挂自己的音色
    speakers = []
    for r in rows:
        who = cell(r, "说话人")
        if cell(r, "台词") and who and not PLACEHOLDER.fullmatch(who) and who not in speakers:
            speakers.append(who)
    for who in speakers:
        hooked = any(kind_of(c, amap) == "音频" and amap.get(c, {}).get("主体") == who for c in codes)
        if hooked:
            continue
        have = [c for c, a in amap.items() if kind_of(c, amap) == "音频" and a["主体"] == who]
        fix = f"把 `@{have[0]}` 加进这条的参考素材" if have else "编号表里也没有 TA 的音色，先做音色参考"
        warns.append(f"{clip_name}：{who} 有台词，但这条没挂 TA 的音色 —— {fix}，否则会用系统默认音。")

    # 一条生成只能一种语言
    langs = []
    for r in rows:
        lg = language_of(cell(r, "台词"))
        if lg and lg not in langs:
            langs.append(lg)
    if len(langs) > 1:
        warns.append(f"{clip_name}：台词混了 {' / '.join(langs)}。一条生成只能一种语言，拆成两条。")

    # --- 输出 ---
    prompt = ["【素材指代】"]
    prompt += [asset_clause(c, slots[c], amap) for c in ordered] or ["（无）"]
    prompt += ["", "【概述】", overview, "", "【时间戳】", *lines, "", "【守则】", rule]
    prompt_text = "\n".join(prompt)

    length = len(re.sub(r"\s", "", prompt_text))
    if length > PROMPT_MAX:
        warns.append(f"{clip_name}：提示词 {length} 字，超过 {PROMPT_MAX} 字。先砍形容词和重复约束，别砍时间戳和台词。")
    elif length < PROMPT_MIN and total >= 8:
        tips.append(f"{clip_name}：{fmt_t(total)} 秒的生成只有 {length} 字提示词，低于官方参考区间 {PROMPT_MIN}–{PROMPT_MAX} 字。建议补动作细节，不然模型会自己加戏。")

    scene = next((cell(r, "场景") for r in rows if cell(r, "场景")), "")
    out = []
    out.append(f"\n---\n\n## {clip_name} · {fmt_t(total)} 秒 · {scene}\n")
    if len(seg_ids) > 1:
        out.append(f"> **合并生成**：{' + '.join(seg_ids)} → 输出文件名 `{clip_name}.mp4`\n")
    note = next((cell(r, "备注") for r in rows if cell(r, "备注")), "")
    if note:
        out.append(f"> 备注：{note}\n")

    out.append("**上传清单**（按这个顺序上传；提示词里的 `@编号` 是上传槽位，不是编号表编号）\n")
    if ordered:
        out.append("| 顺序 | 提示词里写 | 编号表 | 主体 | 文件 |")
        out.append("|---|---|---|---|---|")
        for i, c in enumerate(ordered, 1):
            a = amap.get(c, {})
            fn = os.path.basename(a.get("文件", "")) or "?"
            out.append(f"| {i} | @{slots[c]} | {c} | {a.get('主体','?')} | {fn} |")
    else:
        out.append("（无）")

    out.append("\n```text")
    out.append(prompt_text)
    out.append("```\n")
    return "\n".join(out), codes


def main():
    ap = argparse.ArgumentParser(description="分镜表 + 素材编号表 → Seedance 2.5 提示词包")
    ap.add_argument("--shots", required=True, help="分镜表 CSV")
    ap.add_argument("--assets", required=True, help="素材编号表 CSV")
    ap.add_argument("--out", default="", help="输出 md 文件；不填则打印到 stdout")
    ap.add_argument("--title", default="", help="提示词包标题")
    ap.add_argument("--ratio", default="9:16")
    ap.add_argument("--res", default="720P")
    ap.add_argument("--cps", type=float, default=5.0, help="台词语速（字/秒），默认 5")
    args = ap.parse_args()

    if not os.path.isfile(args.shots):
        raise SystemExit(f"[错误] 找不到分镜表：{args.shots}")
    if not os.path.isfile(args.assets):
        raise SystemExit(f"[错误] 找不到素材编号表：{args.assets}")
    if args.cps <= 0:
        raise SystemExit("[错误] --cps 必须大于 0")

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
    warns, tips, used_codes = [], [], []
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
        f"> 画幅 {args.ratio} · 分辨率 {args.res} · **开启声音** · 逐条生成后进剪辑软件拼接。\n",
        "> **两层编号**：提示词里的 `@图片N / @音频N` 是**这一条的上传槽位**，按每条的「上传清单」顺序上传；"
        "编号表里的编号只用于管理。上传顺序固定为 **图片 → 视频 → 音频**，音色永远排在所有画面素材之后。\n",
        "> 音色绑定：每个角色单独上传音色并**逐角色勾选「仅当前角色可用」**。\n",
        "\n**生成顺序**：先只跑第 1 条校准脸/画风/音色 → 再跑所有有台词的条 → 最后跑过场条。\n",
    ]

    for run in runs:
        clip_name = run[0]
        merged = []
        for sid in run:
            merged.extend(groups[sid])
        block, codes = build_clip(merged, clip_name, amap, args.ratio, args.cps, warns, tips)
        parts.append(block)
        for c in codes:
            if c not in used_codes:
                used_codes.append(c)

    # --- 附录 ---
    parts.append("\n---\n\n## 附：本集素材清单（编号表编号）\n")
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
        parts.append("- ✅ 无问题：时间戳无重叠、台词时长够、总时长在上限内、素材编号全部命中、说话人都挂了音色、单条单语言。")
    for t in tips:
        parts.append(f"- ℹ️ {t}")

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
        for t in tips:
            print(f"[i] {t}")
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
