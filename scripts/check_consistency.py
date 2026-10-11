#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_consistency.py —— AI漫剧 多集一致性检查

为什么需要：
    单集做没问题，做到第 8 集就会发现——角色脸换了、素材编号重编了、
    说话人写成"角色2"、第 3 集的文件名格式和第 5 集不一样。
    这些漂移单看每一集都看不出来，堆到 20 集就成了灾难。

这个脚本把所有分镜表 + 素材编号表扫一遍，把漂移点全部找出来。

用法：
    python check_consistency.py --project .
    python check_consistency.py --project "D:\\我的漫剧项目"
    python check_consistency.py --project . --out 一致性报告.md
    python check_consistency.py --project . --cps 4.5      # 真人风台词语速

可选：项目里有 *剧情状态表*.csv 时，会检查「第 N 集结尾」和「第 N+1 集开头」
每个角色的服装、伤妆、道具、昼夜、天气、场景是否接得上。列名见
assets/剧情状态表模板.csv。

退出码：
    0 = 没有错误      1 = 有错误（可放进发布前的自动检查）
"""

import argparse
import csv
import os
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


# ---------------------------------------------------------------- 常量

SKIP_DIRS = {".git", ".dsh", ".agents", "node_modules", "__pycache__",
             ".idea", ".vscode", ".svn"}

REQUIRED_COLS = ["段号", "时间区间", "画面内容"]
RECOMMENDED_COLS = ["段时长", "段概述", "场景", "景别", "运镜", "动作细节",
                    "台词", "说话人", "音效", "参考素材", "段守则", "合并到"]

ID_RE = re.compile(r"^(图片|音频|视频)(\d+)$")
PLACEHOLDER = re.compile(r"^(角色|人物)\s*\d+$")
STATE_FIELDS = ("服装版本", "伤妆/状态", "手持道具", "昼夜", "天气", "所在场景")
SEG_RE = re.compile(r"^seg(\d+)$", re.IGNORECASE)
NAME_HINT = re.compile(r"_(v\d+|\d+s)\.[A-Za-z0-9]+$")  # 音色参考用时长当后缀：林晚_音色参考_12s.wav
REF_SPLIT = re.compile(r"[;；,，、/|\s]+")

ALIAS = {
    "段号": ("段号", "镜头", "镜号", "段"),
    "时间区间": ("时间区间", "时间段", "时间", "区间"),
    "段时长": ("段时长", "时长"),
    "段概述": ("段概述", "概述", "内容概述"),
    "场景": ("场景", "地点"),
    "景别": ("景别",),
    "运镜": ("运镜", "镜头运动"),
    "画面内容": ("画面内容", "画面", "内容"),
    "动作细节": ("动作细节", "动作"),
    "台词": ("台词", "对白", "说白"),
    "说话人": ("说话人", "角色", "人物"),
    "音效": ("音效", "声音"),
    "参考素材": ("参考素材", "参考", "素材", "参考图"),
    "段守则": ("段守则", "守则", "限定要求"),
    "合并到": ("合并到", "并入", "合并"),
}


# ---------------------------------------------------------------- 工具

class Report:
    def __init__(self):
        self.errors = []
        self.warns = []
        self.infos = []

    def err(self, msg):
        self.errors.append(msg)

    def warn(self, msg):
        self.warns.append(msg)

    def info(self, msg):
        self.infos.append(msg)

    @property
    def ok(self):
        return not self.errors


def read_csv(path):
    """依次尝试多种编码读 CSV。"""
    last = None
    for enc in ("utf-8-sig", "utf-8", "gbk", "cp936", "latin-1"):
        try:
            with open(path, "r", encoding=enc, newline="") as f:
                return list(csv.DictReader(f)), enc
        except (UnicodeDecodeError, UnicodeError) as e:
            last = e
    raise RuntimeError("无法解码 CSV：%s（%s）" % (path, last))


def norm_row(row):
    out = {}
    for k, v in row.items():
        if k is None:
            continue
        key = str(k).replace("\ufeff", "").strip().replace("\n", "")
        val = "" if v is None else str(v).strip()
        if key and key not in out:
            out[key] = val
    return out


def cell(row, field, default=""):
    for name in ALIAS.get(field, (field,)):
        if name in row and row[name] != "":
            return row[name]
    return default


def has_col(header, field):
    return any(name in header for name in ALIAS.get(field, (field,)))


def walk(root, needle):
    """在 root 下找文件名含 needle 的 csv（跳过 .git / .dsh 等）。"""
    hits = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            if fn.lower().endswith(".csv") and needle in fn:
                hits.append(Path(dirpath) / fn)
    return sorted(hits)


def walk_names(root):
    """收集项目里所有文件名，用于查资产文件是否存在。"""
    names = set()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            names.add(fn)
    return names


def parse_sec(tok):
    tok = tok.strip()
    if not tok:
        raise ValueError("empty")
    if ":" in tok:
        m, s = tok.split(":", 1)
        return int(m) * 60 + float(s)
    return float(tok)


def parse_range(text):
    """解析 '0s-3s' / '7.5s-10s' / '0:03-0:06'。返回 (start, end) 或 None。"""
    if not text:
        return None
    t = text
    for ch in ("～", "~", "—", "–", "－", "至"):
        t = t.replace(ch, "-")
    t = t.replace("s", "").replace("S", "").replace("秒", "")
    t = re.sub(r"\s+", "", t)
    if "-" not in t:
        return None
    a, b = t.split("-", 1)
    try:
        s, e = parse_sec(a), parse_sec(b)
    except ValueError:
        return None
    if e < s:
        return None
    return (s, e)


def split_refs(text):
    """'@图片1;@图片6' → ['图片1','图片6']，带不带 @ 都认。"""
    if not text:
        return []
    out = []
    for part in REF_SPLIT.split(text):
        p = part.strip().lstrip("@").strip()
        if p:
            out.append(p)
    return out


def id_sort_key(aid):
    m = ID_RE.match(aid)
    if not m:
        return (9, 0, aid)
    kind_rank = {"图片": 0, "视频": 1, "音频": 2}.get(m.group(1), 8)
    return (kind_rank, int(m.group(2)), aid)


def fmt_t(x):
    return ("%g" % round(x, 2))


def char_count(s):
    n = 0.0
    for ch in s:
        n += 1.0 if ord(ch) > 127 else 0.5
    return n


# ---------------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser(
        description="AI漫剧 多集一致性检查（分镜表 + 素材编号表）")
    ap.add_argument("--project", default=".",
                    help="项目根目录，默认当前目录")
    ap.add_argument("--assets", default=None,
                    help="指定素材编号表（默认自动查找 *素材编号表*.csv）")
    ap.add_argument("--shots", default=None, nargs="*",
                    help="指定分镜表（默认自动查找 *分镜表*.csv）")
    ap.add_argument("--out", default=None,
                    help="把报告写到文件（不带则打印到屏幕）")
    ap.add_argument("--cps", type=float, default=5.0,
                    help="台词语速（字/秒），默认 5；真人风建议 4–4.5")
    args = ap.parse_args()

    root = Path(args.project).expanduser().resolve()
    if not root.is_dir():
        print("[X] 不是目录：%s" % root)
        return 2
    if args.cps <= 0:
        print("[X] --cps 必须大于 0")
        return 2

    rep = Report()
    lines = []
    add = lines.append

    add("=" * 68)
    add(" AI漫剧 · 多集一致性检查报告")
    # 显示用户传入的路径，不展开成本机绝对路径（报告常被提交进仓库）
    add(" 项目目录：%s" % args.project)
    add(" 检查时间：%s" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    add("=" * 68)
    add("")

    # ---------- 1. 发现文件 ----------
    if args.assets:
        asset_files = [Path(args.assets).expanduser().resolve()]
    else:
        asset_files = walk(root, "素材编号表")

    if args.shots:
        shot_files = [Path(p).expanduser().resolve() for p in args.shots]
    else:
        shot_files = walk(root, "分镜表")

    bible_files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            if fn.lower().endswith(".md") and ("系列圣经" in fn or "项目卡" in fn):
                bible_files.append(Path(dirpath) / fn)

    add("【项目扫描】")
    add("  素材编号表：%d 个" % len(asset_files))
    for p in asset_files:
        add("    - %s" % p.relative_to(root) if p.is_relative_to(root) else "    - %s" % p)
    add("  分镜表：%d 个" % len(shot_files))
    for p in shot_files:
        try:
            rel = p.relative_to(root)
        except ValueError:
            rel = p
        add("    - %s" % rel)
    add("  系列圣经/项目卡：%d 份" % len(bible_files))
    add("")

    if not shot_files:
        rep.warn("没有找到任何 *分镜表*.csv —— 请确认文件命名含「分镜表」三个字")
    if not asset_files:
        rep.warn("没有找到任何 *素材编号表*.csv —— 跨集一致性检查会不完整")

    # ---------- 2. 读素材编号表 ----------
    assets = {}          # 编号 -> {file, subject, kind, usage, notuse, src}
    reg_conflict = []    # (编号, 主体A, 来源A, 主体B, 来源B)

    for path in asset_files:
        try:
            rows, _enc = read_csv(path)
        except Exception as e:
            rep.err("读不了素材编号表：%s（%s）" % (path.name, e))
            continue

        for i, raw in enumerate(rows, start=2):
            row = norm_row(raw)
            aid = cell(row, "编号")
            if not aid and not any(row.values()):
                continue
            if not aid:
                rep.err("素材编号表 %s 第 %d 行：编号为空" % (path.name, i))
                continue

            m = ID_RE.match(aid)
            if not m:
                rep.warn("素材编号表 %s 第 %d 行：编号「%s」不符合「图片N/视频N/音频N」格式"
                         % (path.name, i, aid))

            subject = cell(row, "主体", "") or cell(row, "名称", "")
            kind = cell(row, "类型", "")
            fname = cell(row, "文件", "") or cell(row, "文件名", "")
            usage = cell(row, "用途", "")
            notuse = cell(row, "不采用", "")

            if aid in assets:
                old = assets[aid]
                if old["subject"] and subject and old["subject"] != subject:
                    reg_conflict.append((aid, old["subject"], old["src"],
                                         subject, path.name))
                else:
                    rep.warn("素材编号表：编号「%s」在 %s 和 %s 里重复登记（主体相同，已合并）"
                             % (aid, old["src"], path.name))
                # 补充缺失字段
                for k, v in (("file", fname), ("kind", kind),
                             ("usage", usage), ("notuse", notuse)):
                    if not old.get(k) and v:
                        old[k] = v
                continue

            assets[aid] = {
                "file": fname, "subject": subject, "kind": kind,
                "usage": usage, "notuse": notuse, "src": path.name,
                "line": i,
            }
            if not subject:
                rep.warn("素材编号表 %s 第 %d 行：编号「%s」没有填主体"
                         % (path.name, i, aid))

    for aid, sa, src_a, sb, src_b in reg_conflict:
        rep.err("编号冲突：「%s」在 %s 里是「%s」，在 %s 里却是「%s」 —— "
                "编号全局唯一，同一个编号不能指两个主体"
                % (aid, src_a, sa, src_b, sb))

    # 编号重复定义检查（同一文件内）
    # 已在上面合并处理

    # ---------- 3. 读分镜表 ----------
    episodes = []   # [{path, rel, rows, segs, cols, total, lines, speakers}]
    file_index = walk_names(root)

    for path in shot_files:
        try:
            rows, _enc = read_csv(path)
        except Exception as e:
            rep.err("读不了分镜表：%s（%s）" % (path.name, e))
            continue
        if not rows:
            rep.warn("分镜表是空的：%s" % path.name)
            continue

        header = [str(k).replace("\ufeff", "").strip()
                  for k in rows[0].keys() if k is not None]
        nrows = [norm_row(r) for r in rows]
        nrows = [r for r in nrows if any(r.values())]

        try:
            rel = path.relative_to(root)
        except ValueError:
            rel = path

        missing_req = [c for c in REQUIRED_COLS if not has_col(header, c)]
        missing_rec = [c for c in RECOMMENDED_COLS if not has_col(header, c)]
        if missing_req:
            rep.err("%s：缺少必需列 %s" % (rel, "、".join(missing_req)))
        if missing_rec:
            rep.warn("%s：缺少建议列 %s（不影响检查，但会影响脚本生成提示词）"
                     % (rel, "、".join(missing_rec)))

        # 按段号归组
        segs = []
        order = []
        by_seg = {}
        for idx, row in enumerate(nrows, start=2):
            sid = cell(row, "段号")
            if not sid:
                rep.warn("%s 第 %d 行：段号为空" % (rel, idx))
                continue
            if sid not in by_seg:
                by_seg[sid] = []
                order.append(sid)
            by_seg[sid].append((idx, row))

        for sid in order:
            entries = by_seg[sid]
            seg_rows = [r for _i, r in entries]
            starts, ends = [], []
            for _i, r in entries:
                pr = parse_range(cell(r, "时间区间"))
                if pr:
                    starts.append(pr[0])
                    ends.append(pr[1])
            total = (max(ends) - min(starts)) if starts and ends else 0.0

            speakers = set()
            nlines = 0
            nchars = 0.0
            for r in seg_rows:
                sp = cell(r, "说话人")
                if sp:
                    speakers.add(sp)
                txt = cell(r, "台词")
                if txt:
                    nlines += 1
                    nchars += char_count(txt)

            segs.append({
                "id": sid, "rows": seg_rows, "entries": entries,
                "start": min(starts) if starts else 0.0,
                "end": max(ends) if ends else 0.0,
                "dur": total, "speakers": speakers,
                "nlines": nlines, "nchars": nchars,
            })

        episodes.append({
            "path": path, "rel": rel, "segs": segs, "header": header,
            "total": sum(s["dur"] for s in segs),
            "lines": sum(s["nlines"] for s in segs),
        })

    # ---------- 4. 跨集检查 ----------
    used_ids = defaultdict(set)      # 编号 -> {集名}
    ref_count = defaultdict(int)
    subject_type_ids = defaultdict(set)   # (主体, 类型) -> {编号}
    all_speakers = defaultdict(set)       # 说话人 -> {集名}

    for ep in episodes:
        rel = ep["rel"]

        # 4.1 段号规范
        for s in ep["segs"]:
            if not SEG_RE.match(s["id"]):
                rep.warn("%s：段号「%s」不符合 segNN 格式（应为 seg01、seg02…）"
                         % (rel, s["id"]))
        nums = []
        bad = False
        for s in ep["segs"]:
            m = SEG_RE.match(s["id"])
            if not m:
                bad = True
                break
            nums.append(int(m.group(1)))
        if nums and not bad:
            expect = list(range(1, len(nums) + 1))
            if nums != expect:
                rep.warn("%s：段号不连续 —— 实际 %s，应为 %s"
                         % (rel, [("seg%02d" % n) for n in nums],
                            [("seg%02d" % n) for n in expect]))

        # 4.2 引用有效性
        for s in ep["segs"]:
            any_ref = False
            for rowno, r in s["entries"]:
                refs = split_refs(cell(r, "参考素材"))
                for aid in refs:
                    any_ref = True
                    ref_count[aid] += 1
                    used_ids[aid].add(str(rel))
                    if aid not in assets:
                        rep.err("%s 第 %d 行（%s）：引用「%s」但素材编号表里没有这个编号"
                                % (rel, rowno, s["id"], aid))
            # 整条无素材
            if not any_ref:
                merged = any(cell(r, "合并到") for _i, r in s["entries"])
                note = "（会被合并，可忽略）" if merged else ""
                if note:
                    rep.info("%s %s：整段没有参考素材%s" % (rel, s["id"], note))
                else:
                    rep.warn("%s %s：整段没有参考素材 —— "
                             "角色一致性没有锚点，脸会飘" % (rel, s["id"]))

        # 4.3 说话人登记
        for s in ep["segs"]:
            for sp in s["speakers"]:
                all_speakers[sp].add(str(rel))
                if PLACEHOLDER.match(sp):
                    rep.err("%s %s：说话人写成了「%s」—— "
                            "必须写真实角色名，否则音色会串" % (rel, s["id"], sp))

        # 4.3b 每条生成里，有台词的说话人必须挂自己的音色（按「合并到」把段连成一条）
        runs, cur = [], []
        for idx, s in enumerate(ep["segs"]):
            cur.append(s)
            nxt = ep["segs"][idx + 1]["id"] if idx + 1 < len(ep["segs"]) else None
            target = next((cell(r, "合并到") for r in s["rows"] if cell(r, "合并到")), "")
            if not (target and target == nxt):
                runs.append(cur)
                cur = []
        for run in runs:
            refs = set()
            talkers = set()
            for s in run:
                for r in s["rows"]:
                    refs.update(split_refs(cell(r, "参考素材")))
                    sp = cell(r, "说话人")
                    if cell(r, "台词") and sp and not PLACEHOLDER.match(sp):
                        talkers.add(sp)
            for sp in sorted(talkers):
                hooked = any(aid in assets and assets[aid]["subject"] == sp
                             and ID_RE.match(aid) and ID_RE.match(aid).group(1) == "音频"
                             for aid in refs)
                if not hooked:
                    rep.warn("%s %s：「%s」有台词，但这条生成没挂 TA 的音频N 音色 —— "
                             "会用系统默认音" % (rel, run[0]["id"], sp))

        # 4.4 台词密度
        for s in ep["segs"]:
            for rowno, r in s["entries"]:
                txt = cell(r, "台词")
                if not txt:
                    continue
                pr = parse_range(cell(r, "时间区间"))
                if not pr:
                    continue
                span = pr[1] - pr[0]
                need = char_count(txt) / args.cps + 0.5
                if need > span + 0.01:
                    rep.err("%s 第 %d 行（%s）：台词「%s」约需 %.1fs，区间只有 %.1fs"
                            % (rel, rowno, s["id"],
                               txt if len(txt) <= 14 else txt[:14] + "…",
                               need, span))

                if span > 12.5:
                    rep.warn("%s %s：单行区间 %.1fs 太长（建议 ≤12s）"
                             % (rel, s["id"], span))

        # 4.5 段内时间戳重叠
        for s in ep["segs"]:
            ivs = []
            for rowno, r in s["entries"]:
                pr = parse_range(cell(r, "时间区间"))
                if pr:
                    ivs.append((pr[0], pr[1], rowno))
            ivs.sort()
            for a, b in zip(ivs, ivs[1:]):
                if b[0] < a[1] - 1e-6:
                    rep.err("%s %s：时间戳重叠 —— 第 %d 行 %ss-%ss 与 第 %d 行 %ss-%ss"
                            % (rel, s["id"], a[2], fmt_t(a[0]), fmt_t(a[1]),
                               b[2], fmt_t(b[0]), fmt_t(b[1])))

    # 4.6 主体 + 类型 重复资产
    for aid, a in assets.items():
        if a["subject"] and a["kind"]:
            subject_type_ids[(a["subject"], a["kind"])].add(aid)
    for (subj, kind), ids in sorted(subject_type_ids.items()):
        if len(ids) > 1:
            rep.warn("「%s」的「%s」有 %d 个资产（%s）—— "
                     "如果是有意为之（不同服装/状态）可忽略；"
                     "如果是重出过一遍，就会跨集换脸"
                     % (subj, kind, len(ids),
                        "、".join(sorted(ids, key=id_sort_key))))

    # 4.7 孤儿资产
    orphans = [aid for aid in assets if aid not in used_ids]
    if orphans:
        rep.info("从未被任何分镜表引用的资产 %d 个：%s"
                 % (len(orphans),
                    "、".join(sorted(orphans, key=id_sort_key))))

    # 4.8 音色覆盖
    for sp, eps in sorted(all_speakers.items()):
        if PLACEHOLDER.match(sp):
            continue
        has_voice = any(a["subject"] == sp and
                        ID_RE.match(aid) and
                        ID_RE.match(aid).group(1) == "音频"
                        for aid, a in assets.items())
        if not has_voice:
            # 该角色是否真的说过话？all_speakers 只收有说话人的
            rep.warn("角色「%s」有台词，但素材编号表里没有 TA 的「音频N」音色参考 "
                     "—— 会导致该角色用系统默认音" % sp)

    # 4.9 资产文件存在性 + 命名规范
    missing_files = []
    bad_names = []
    for aid in sorted(assets, key=id_sort_key):
        a = assets[aid]
        fn = a["file"]
        if not fn:
            continue
        base = os.path.basename(fn)
        if base not in file_index:
            missing_files.append("%s（%s）" % (base, aid))
        if not NAME_HINT.search(base):
            bad_names.append("%s（%s）" % (base, aid))
    if missing_files:
        rep.info("编号表登记但项目里找不到的文件 %d 个（可能还没生成）：\n      %s"
                 % (len(missing_files), "\n      ".join(missing_files)))
    if bad_names:
        rep.info("文件名不符合「<主体>_<类型>_<变体>_v<序号>.<ext>」（音色参考是 _<时长>s.wav）的 %d 个：\n      %s"
                 % (len(bad_names), "\n      ".join(bad_names)))

    # 4.10 剧情状态表：第 N 集结尾 vs 第 N+1 集开头
    state_files = walk(root, "剧情状态表")
    for sp_path in state_files:
        try:
            srows, _enc = read_csv(sp_path)
        except Exception as e:
            rep.err("读不了剧情状态表：%s（%s）" % (sp_path.name, e))
            continue
        states = {}   # (集号数字, 时点, 角色) -> row
        for i, raw in enumerate(srows, start=2):
            row = norm_row(raw)
            m = re.search(r"\d+", cell(row, "集号"))
            point = cell(row, "时点")
            who = cell(row, "角色")
            if not (m and point and who):
                if any(row.values()):
                    rep.warn("剧情状态表 %s 第 %d 行：集号 / 时点 / 角色 缺一项，跳过"
                             % (sp_path.name, i))
                continue
            if point not in ("开头", "结尾"):
                rep.warn("剧情状态表 %s 第 %d 行：时点写「开头」或「结尾」，现在是「%s」"
                         % (sp_path.name, i, point))
                continue
            states[(int(m.group(0)), point, who)] = row
        for (ep_no, point, who), end in sorted(states.items()):
            if point != "结尾":
                continue
            start = states.get((ep_no + 1, "开头", who))
            if start is None:
                continue
            diffs = []
            for f in STATE_FIELDS:
                a, b = cell(end, f), cell(start, f)
                if a and b and a != b:
                    diffs.append("%s「%s」→「%s」" % (f, a, b))
            if diffs:
                rep.warn("剧情状态接不上：%s 第 %d 集结尾 → 第 %d 集开头，%s"
                         "（如果是剧情里换了，在下一集开头交代清楚）"
                         % (who, ep_no, ep_no + 1, "；".join(diffs)))

    # ---------- 5. 系列圣经锚点 ----------
    bible_anchors = []
    for bp in bible_files[:3]:
        try:
            text = bp.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for line in text.splitlines():
            m = re.match(r"^\s*[-*>]*\s*([^:：|]{2,12})[:：|]\s*(.+?)\s*$", line)
            if not m:
                continue
            key, val = m.group(1).strip(), m.group(2).strip()
            if any(k in key for k in ("画幅", "比例", "画风", "风格",
                                      "主色", "次色", "点缀", "色调",
                                      "光照", "时代", "地域", "禁")):
                bible_anchors.append("%s = %s" % (key, val))
    bible_anchors = list(dict.fromkeys(bible_anchors))[:12]

    # ---------- 6. 输出 ----------
    add("【统计】")
    add("  集数：%d    总段数：%d    总时长：%.1fs    台词行：%d"
        % (len(episodes), sum(len(e["segs"]) for e in episodes),
           sum(e["total"] for e in episodes),
           sum(e["lines"] for e in episodes)))
    add("  资产登记：%d 项（图片 %d / 视频 %d / 音频 %d）"
        % (len(assets),
           sum(1 for a in assets if ID_RE.match(a) and ID_RE.match(a).group(1) == "图片"),
           sum(1 for a in assets if ID_RE.match(a) and ID_RE.match(a).group(1) == "视频"),
           sum(1 for a in assets if ID_RE.match(a) and ID_RE.match(a).group(1) == "音频")))
    add("  被引用的资产：%d 项，总引用次数 %d"
        % (len(used_ids), sum(ref_count.values())))
    add("  出现过的说话人：%s"
        % ("、".join(sorted(all_speakers)) if all_speakers else "（无）"))
    add("")

    for ep in episodes:
        add("    %s —— %d 段 / %.1fs / %d 句台词"
            % (ep["rel"], len(ep["segs"]), ep["total"], ep["lines"]))
    add("")

    if bible_anchors:
        add("【系列圣经锚点】（各集都必须继承，人工核对）")
        for a in bible_anchors:
            add("    %s" % a)
        add("")

    add("【✗ 错误 %d】" % len(rep.errors))
    if rep.errors:
        for i, m in enumerate(rep.errors, 1):
            add("  %d. %s" % (i, m))
    else:
        add("  （无）")
    add("")

    add("【⚠ 警告 %d】" % len(rep.warns))
    if rep.warns:
        for i, m in enumerate(rep.warns, 1):
            add("  %d. %s" % (i, m))
    else:
        add("  （无）")
    add("")

    add("【ℹ 提示 %d】" % len(rep.infos))
    if rep.infos:
        for i, m in enumerate(rep.infos, 1):
            add("  %d. %s" % (i, m))
    else:
        add("  （无）")
    add("")

    add("=" * 68)
    if rep.ok:
        add(" 结论：✓ 通过（%d 个警告 / %d 条提示）" % (len(rep.warns), len(rep.infos)))
    else:
        add(" 结论：✗ 有 %d 个错误需要修（%d 个警告 / %d 条提示）"
            % (len(rep.errors), len(rep.warns), len(rep.infos)))
    add("=" * 68)

    text = "\n".join(lines)

    if args.out:
        out = Path(args.out).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
        print(text)
        print("")
        print("[OK] 报告已写入：%s" % out)
    else:
        print(text)

    return 0 if rep.ok else 1


if __name__ == "__main__":
    sys.exit(main())
