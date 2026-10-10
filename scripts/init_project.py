#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
init_project.py —— 一键建项目骨架：目录 + 模板文件全部按规范放好。

用法：
    # 同人 / 拼贴风 MV 项目（见 references/12、13）
    python init_project.py --type mv --name 夏日信笺

    # 商业漫剧项目（见 assets/项目目录与命名规范.md）
    python init_project.py --type manju --name 她的第二次人生

    --dir    建在哪个文件夹下，默认当前目录
    --role   角色名（可多个），会建好角色文件夹和设定考据文件

已经存在的文件不会被覆盖，可以对旧项目重复运行来补齐缺的目录。
"""

import argparse
import shutil
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

ASSETS = Path(__file__).resolve().parent.parent / "assets"

# (项目内路径, 模板文件名)；模板为 None 表示只建目录
LAYOUT = {
    "mv": {
        "prefix": "同人MV_",
        "dirs": [
            "00_企划", "01_脚本", "02_资产/角色", "02_资产/拼贴/纸张", "02_资产/拼贴/胶带",
            "02_资产/拼贴/贴纸与图标", "02_资产/拼贴/涂鸦笔刷", "02_资产/手写字", "02_资产/风格帧",
            "02_资产/实拍/空镜", "02_资产/实拍/周边", "02_资产/LoRA", "03_关键帧", "04_视频片段",
            "05_音乐声音/原创曲", "05_音乐声音/配音", "06_工程/PR", "06_工程/AE", "06_工程/AE模板",
            "06_工程/LUT", "07_发布",
        ],
        "files": [
            ("00_企划/风格圣经.md", "风格圣经模板.md"),
            ("00_企划/01_企划卡.md", "企划卡模板.md"),
            ("01_脚本/01_MV分镜表.csv", "MV分镜表模板.csv"),
            ("01_脚本/剧情状态表.csv", "剧情状态表模板.csv"),
            ("02_资产/_素材编号表.csv", "素材编号表模板.csv"),
            ("07_发布/数据表.csv", "数据表模板.csv"),
            ("生成日志.csv", "生成日志模板.csv"),
        ],
        "role_dir": "02_资产/角色/{role}",
        "role_file": ("00_企划/设定考据_{role}.md", "设定考据模板.md"),
    },
    "manju": {
        "prefix": "漫剧项目_",
        "dirs": [
            "00_项目卡", "01_剧本", "02_资产/角色", "02_资产/场景", "02_资产/道具",
            "03_提示词", "04_成片/第01集", "05_发布",
        ],
        "files": [
            ("00_项目卡/立项卡.md", "立项卡模板.md"),
            ("00_项目卡/系列圣经.md", "系列圣经模板.md"),
            ("01_剧本/第01集_分镜表.csv", "分镜表模板.csv"),
            ("01_剧本/剧情状态表.csv", "剧情状态表模板.csv"),
            ("02_资产/_素材编号表.csv", "素材编号表模板.csv"),
            ("04_成片/生成日志.csv", "生成日志模板.csv"),
            ("05_发布/数据表.csv", "数据表模板.csv"),
        ],
        "role_dir": "02_资产/角色/{role}",
        "role_file": None,
    },
}


def main():
    ap = argparse.ArgumentParser(description="一键建项目骨架")
    ap.add_argument("--type", required=True, choices=sorted(LAYOUT), help="mv = 同人拼贴风 MV，manju = 商业漫剧")
    ap.add_argument("--name", required=True, help="项目名（剧名 / 系列名）")
    ap.add_argument("--dir", default=".", help="建在哪个文件夹下，默认当前目录")
    ap.add_argument("--role", nargs="*", default=[], help="角色名，可多个")
    args = ap.parse_args()

    spec = LAYOUT[args.type]
    root = Path(args.dir).expanduser() / f"{spec['prefix']}{args.name}"
    made, kept = [], []

    for d in spec["dirs"]:
        (root / d).mkdir(parents=True, exist_ok=True)
    for role in args.role:
        (root / spec["role_dir"].format(role=role)).mkdir(parents=True, exist_ok=True)

    files = list(spec["files"])
    if spec["role_file"]:
        dest, tpl = spec["role_file"]
        files += [(dest.format(role=role), tpl) for role in args.role]

    for dest, tpl in files:
        target = root / dest
        if target.exists():
            kept.append(dest)
            continue
        src = ASSETS / tpl
        if not src.is_file():
            raise SystemExit(f"[错误] 找不到模板 {src}，请确认脚本在仓库的 scripts/ 里运行")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, target)
        made.append(dest)

    print(f"[OK] 项目骨架：{root}")
    for f in made:
        print(f"    + {f}")
    for f in kept:
        print(f"    = {f}（已存在，没动）")
    nxt = ("先填 00_企划/风格圣经.md 和 企划卡，再画 Q 版差分（references/13）"
           if args.type == "mv" else "先填 00_项目卡/立项卡.md，再写剧集大纲（references/01）")
    print(f"[下一步] {nxt}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
