#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fcitx5-phrase —— fcitx5 拼音 / 五笔拼音自定义词组管理器

设计要点
--------
* 以纯文本词组表（默认 ~/.config/fcitx5/custom-phrase.tsv）为唯一数据源，
  二进制用户词库由它派生，可随时重建、迁移、版本管理。
* 拼音可以只写连写形式（weixin），自动按 libime 要求切成 wei'xin；
  不写拼音时，基于 wbpy 主码表里 @ 拼音段做（带词频统计的）单字反查。
* 五笔编码可以自动推断：读取主码表的 [Rule] 公式
    e2 = p11+p12+p21+p22      （二字词）
    e3 = p11+p21+p31+p32      （三字词）
    a4 = p11+p21+p31+n11      （四字及以上）
* 重建时先停 fcitx5，写临时文件后原子替换，重建后重新导出校验，
  校验失败自动回滚备份。兼容旧版 add_phrase.sh 的参数顺序。

作者: 由 AI 协作生成，供 zseagate/fcitx5-custom-phrase 使用
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

VERSION = "2.1.0"

CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
PINYIN_RE = re.compile(r"^[a-zü:']+$")
WUBI_RE = re.compile(r"^[a-y]{1,4}$")

# 主码表 [Rule] 里的公式对应的取码规则（硬编码为五笔标准，防止码表缺 [Rule]）
WUBI_RULES = {
    2: ("p11", "p12", "p21", "p22"),
    3: ("p11", "p21", "p31", "p32"),
}


class PhraseError(Exception):
    """用户可见的可预期错误。"""


# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------

def home_dir() -> Path:
    return Path(os.environ.get("HOME", str(Path.home())))


def config_path() -> Path:
    env = os.environ.get("FCITX5_PHRASE_FILE")
    if env:
        return Path(env).expanduser()
    return home_dir() / ".config/fcitx5/custom-phrase.tsv"


def pinyin_dict_path() -> Path:
    return home_dir() / ".local/share/fcitx5/pinyin/user.dict"


def wbpy_dict_path() -> Path:
    return home_dir() / ".local/share/fcitx5/table/wbpy.user.dict"


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or (home_dir() / ".cache")
    return Path(base) / "fcitx5-phrase"


def state_path() -> Path:
    """记录上次由本工具管理的词组，用于把“删除”同步到二进制词库。"""
    env = os.environ.get("FCITX5_PHRASE_STATE")
    if env:
        return Path(env).expanduser()
    return home_dir() / ".local/share/fcitx5/custom-phrase/managed.json"


def main_dict_path() -> Path:
    return Path(os.environ.get("FCITX5_WBPY_MAIN", "/usr/share/libime/wbpy.main.dict"))


# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------

def is_cjk(ch: str) -> bool:
    return bool(CJK_RE.match(ch))


def is_all_cjk(text: str) -> bool:
    return bool(text) and all(is_cjk(c) for c in text)


def run(cmd: list[str], *, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, check=check,
    )


def require_tools() -> None:
    missing = [c for c in ("libime_pinyindict", "libime_tabledict")
               if shutil.which(c) is None]
    if missing:
        raise PhraseError(
            "找不到 libime 工具: " + ", ".join(missing) +
            "\n请安装: sudo apt install libime-bin   （或随 fcitx5 一起安装）"
        )


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S-%f")


# ---------------------------------------------------------------------------
# 词组表（TSV）
# ---------------------------------------------------------------------------

def validate_phrase(phrase: str) -> str:
    phrase = phrase.strip()
    if not phrase:
        raise PhraseError("词组不能为空")
    if any(c in phrase for c in "\t\r\n"):
        raise PhraseError("词组不能包含制表符或换行")
    if " " in phrase:
        raise PhraseError("词组暂不支持空格（libime 文本格式以空格分隔字段）")
    return phrase


def validate_weight(weight: int) -> int:
    if weight < 0:
        raise PhraseError("权重不能为负数")
    return int(weight)


def read_phrase_file(path: Path | None = None) -> list[dict]:
    """读取 TSV，返回 [{phrase, pinyin, wubi, weight}]。"""
    path = path or config_path()
    if not path.exists():
        return []
    entries: list[dict] = []
    seen: dict[str, int] = {}
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "\t" in raw:
            parts = raw.split("\t")
        else:
            parts = raw.split()
        parts = [p.strip() for p in parts]
        while len(parts) < 4:
            parts.append("")
        phrase = validate_phrase(parts[0])
        pinyin = parts[1]
        wubi = parts[2].lower()
        weight_s = parts[3]
        try:
            weight = validate_weight(int(weight_s)) if weight_s else 100
        except ValueError:
            raise PhraseError(f"{path}:{lineno}: 权重不是整数: {weight_s!r}")
        entry = {"phrase": phrase, "pinyin": pinyin, "wubi": wubi, "weight": weight}
        if phrase in seen:
            entries[seen[phrase]] = entry      # 后者覆盖前者
        else:
            seen[phrase] = len(entries)
            entries.append(entry)
    return entries


def write_phrase_file(entries: list[dict], path: Path | None = None) -> None:
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# fcitx5-custom-phrase 词组表",
        "# 格式: 词组<TAB>拼音<TAB>五笔编码<TAB>权重",
        "# 拼音可连写(weixin)，可留空自动推断；五笔编码可留空自动推断。",
    ]
    for e in entries:
        lines.append("\t".join([
            e["phrase"], e.get("pinyin", ""), e.get("wubi", ""),
            str(e.get("weight", 100)),
        ]))
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def read_managed() -> set[str]:
    try:
        data = json.loads(state_path().read_text(encoding="utf-8"))
        return set(data.get("phrases", []))
    except Exception:
        return set()


def write_managed(phrases: set[str]) -> None:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(
        {"version": 1, "phrases": sorted(phrases)},
        ensure_ascii=False, indent=2,
    ), encoding="utf-8")
    os.replace(tmp, path)


def upsert(entries: list[dict], new: dict) -> list[dict]:
    for i, e in enumerate(entries):
        if e["phrase"] == new["phrase"]:
            entries[i] = new
            return entries
    entries.append(new)
    return entries


# ---------------------------------------------------------------------------
# 主码表缓存与索引
# ---------------------------------------------------------------------------

def _meta_file() -> Path:
    return cache_dir() / "main.meta.json"


def _main_dump_file() -> Path:
    return cache_dir() / "main.txt"


def ensure_main_dump() -> Path:
    """把系统 wbpy 主码表导出为文本并缓存（按 mtime/size 失效）。"""
    src = main_dict_path()
    if not src.exists():
        raise PhraseError(
            f"找不到五笔拼音主码表: {src}\n"
            "请确认已安装 libime-data（sudo apt install libime-data）"
        )
    st = src.stat()
    cache_dir().mkdir(parents=True, exist_ok=True)
    meta = {"mtime": st.st_mtime_ns, "size": st.st_size}
    try:
        old = json.loads(_meta_file().read_text())
    except Exception:
        old = None
    dump = _main_dump_file()
    if old == meta and dump.exists():
        return dump

    fd, tmp_name = tempfile.mkstemp(dir=cache_dir(), prefix="main.", suffix=".txt")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        cp = run(["libime_tabledict", "-d", str(src), str(tmp)])
        if cp.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
            raise PhraseError(
                "导出主码表失败: " + (cp.stderr.strip() or "未知错误")
            )
        os.replace(tmp, dump)
        _meta_file().write_text(json.dumps(meta))
    finally:
        tmp.unlink(missing_ok=True)
    return dump


def _pinyin_index_file() -> Path:
    return cache_dir() / "pinyin_index.json"


def _wubi_index_file() -> Path:
    return cache_dir() / "wubi_index.json"


def _index_meta() -> dict:
    st = main_dict_path().stat()
    return {"version": 2, "mtime": st.st_mtime_ns, "size": st.st_size}


def _segment_exact(s: str, n: int, by_first: dict[str, list[str]]) -> list[str] | None:
    """把无分隔拼音切成恰好 n 个音节；失败返回 None。"""
    memo: dict[tuple[int, int], list[str] | None] = {}

    def rec(i: int, k: int) -> list[str] | None:
        key = (i, k)
        if key in memo:
            return memo[key]
        if i == len(s):
            return [] if k == n else None
        if k >= n:
            memo[key] = None
            return None
        for syl in by_first.get(s[i], ()):
            if s.startswith(syl, i):
                rest = rec(i + len(syl), k + 1)
                if rest is not None:
                    memo[key] = [syl] + rest
                    return memo[key]
        memo[key] = None
        return None

    return rec(0, 0)


def build_indexes() -> tuple[dict, dict]:
    """构建拼音音节/字频索引与五笔单字编码索引，带磁盘缓存。"""
    dump = ensure_main_dump()
    meta = _index_meta()
    pfile, wfile = _pinyin_index_file(), _wubi_index_file()

    pinyin_index = wubi_index = None
    try:
        pj = json.loads(pfile.read_text())
        if pj.get("meta") == meta:
            pinyin_index = pj
    except Exception:
        pass
    try:
        wj = json.loads(wfile.read_text())
        if wj.get("meta") == meta:
            wubi_index = wj
    except Exception:
        pass
    if pinyin_index is not None and wubi_index is not None:
        return pinyin_index, wubi_index

    lines = dump.read_text(encoding="utf-8", errors="replace").splitlines()

    # --- 第一遍：单字 @ 条目给出全部合法音节 ---
    syllables: set[str] = set()
    for line in lines:
        if line.startswith("@"):
            key, _, val = line[1:].partition(" ")
            if len(val) == 1 and key:
                syllables.add(key)

    by_first: dict[str, list[str]] = defaultdict(list)
    for syl in syllables:
        by_first[syl[0]].append(syl)
    for k in by_first:
        by_first[k].sort(key=len, reverse=True)

    # --- 第二遍：用“多字词的音节数 = 汉字数”对齐，统计每个字的读音频次 ---
    char_freq: dict[str, Counter] = defaultdict(Counter)
    phrase_freq: dict[str, Counter] = defaultdict(Counter)
    wubi_codes: dict[str, list[str]] = defaultdict(list)
    for line in lines:
        if not line or " " not in line:
            continue
        if line.startswith("@"):
            key, _, val = line[1:].partition(" ")
            if len(val) >= 2 and is_all_cjk(val):
                phrase_freq[val][key] += 1
                seg = _segment_exact(key, len(val), by_first)
                if seg:
                    for ch, syl in zip(val, seg):
                        char_freq[ch][syl] += 1
        else:
            code, _, val = line.partition(" ")
            if len(val) == 1 and is_cjk(val):
                wubi_codes[val].append(code)

    # 每个字保留前 4 个读音
    char_pinyin = {
        ch: [s for s, _ in counter.most_common(4)]
        for ch, counter in char_freq.items()
    }

    # 五笔单字编码：优先 4 码全码，否则取最长
    char_wubi: dict[str, str] = {}
    for ch, codes in wubi_codes.items():
        four = [c for c in codes if len(c) == 4 and WUBI_RE.match(c)]
        if four:
            char_wubi[ch] = four[0]
        else:
            valid = [c for c in codes if WUBI_RE.match(c)]
            if valid:
                char_wubi[ch] = max(valid, key=len)

    # 整词读音：取码表里出现最多的那个读音
    phrase_pinyin = {
        phrase: counter.most_common(1)[0][0]
        for phrase, counter in phrase_freq.items()
    }
    pinyin_index = {
        "meta": meta,
        "syllables": sorted(syllables),
        "char_pinyin": char_pinyin,
        "phrase_pinyin": phrase_pinyin,
    }
    wubi_index = {"meta": meta, "char_wubi": char_wubi}

    cache_dir().mkdir(parents=True, exist_ok=True)
    pfile.write_text(json.dumps(pinyin_index, ensure_ascii=False))
    wfile.write_text(json.dumps(wubi_index, ensure_ascii=False))
    return pinyin_index, wubi_index


# ---------------------------------------------------------------------------
# 拼音 / 五笔 推断
# ---------------------------------------------------------------------------

def _syllable_maps(pinyin_index: dict) -> dict[str, list[str]]:
    by_first: dict[str, list[str]] = defaultdict(list)
    for syl in pinyin_index["syllables"]:
        by_first[syl[0]].append(syl)
    for k in by_first:
        by_first[k].sort(key=len, reverse=True)
    return by_first


def segment_pinyin(raw: str, pinyin_index: dict) -> str:
    """把用户输入规范化为 libime 的 wei'xin 形式。"""
    s = raw.strip().lower().replace("ü", "v").replace("u:", "v")
    s = re.sub(r"[\s\-]+", "", s)
    if not s:
        raise PhraseError("拼音为空")
    if not PINYIN_RE.match(s):
        raise PhraseError(f"拼音包含非法字符: {raw!r}（只允许 a-z、ü 和 '）")

    syllables = set(pinyin_index["syllables"])
    if "'" in s:
        parts = [p for p in s.split("'") if p]
        bad = [p for p in parts if p not in syllables]
        if bad:
            raise PhraseError(
                "无法识别的拼音音节: " + ", ".join(bad) +
                "\n（可运行 fcitx5-phrase doctor 检查码表，或手动用 ' 分隔）"
            )
        return "'".join(parts)

    by_first = _syllable_maps(pinyin_index)
    # DP：音节数最少优先，其次按字典序，保证 xian -> xian 而不是 xi'an
    best: list[tuple[int, tuple[str, ...]] | None] = [None] * (len(s) + 1)
    best[0] = (0, ())
    for i in range(len(s)):
        if best[i] is None:
            continue
        for syl in by_first.get(s[i], ()):
            if not s.startswith(syl, i):
                continue
            j = i + len(syl)
            cand = (best[i][0] + 1, best[i][1] + (syl,))
            if best[j] is None or cand < best[j]:
                best[j] = cand
    if best[len(s)] is None:
        raise PhraseError(
            f"无法把拼音 {raw!r} 切成合法音节。\n"
            "请手动用 ' 分隔，例如 xi'an（西安）或 fang'an（方案）。"
        )
    return "'".join(best[len(s)][1])


def _align_by_chars(phrase: str, key: str,
                    char_pinyin: dict[str, list[str]]) -> list[str] | None:
    """用每个字的候选读音去对齐整词连写拼音，避免 xian 被切成 xia'n。"""
    memo: dict[tuple[int, int], list[str] | None] = {}

    def rec(ci: int, pos: int) -> list[str] | None:
        st = (ci, pos)
        if st in memo:
            return memo[st]
        if ci == len(phrase):
            return [] if pos == len(key) else None
        ch = phrase[ci]
        for syl in char_pinyin.get(ch, ()):
            if key.startswith(syl, pos):
                rest = rec(ci + 1, pos + len(syl))
                if rest is not None:
                    memo[st] = [syl] + rest
                    return memo[st]
        memo[st] = None
        return None

    return rec(0, 0)


def auto_pinyin(phrase: str, pinyin_index: dict) -> str:
    """推断拼音：优先整词读音，其次按字频最高的单字读音。"""
    # 1) 整词读音（能正确处理“重要/银行/西安”这类多音字）
    whole = pinyin_index.get("phrase_pinyin", {}).get(phrase)
    if whole:
        seg = _align_by_chars(phrase, whole, pinyin_index["char_pinyin"])
        if not seg:
            by_first = _syllable_maps(pinyin_index)
            seg = _segment_exact(whole, len(phrase), by_first)
        if seg:
            return "'".join(seg)

    # 2) 退化为逐字取最高频读音
    char_pinyin = pinyin_index["char_pinyin"]
    out: list[str] = []
    for ch in phrase:
        readings = char_pinyin.get(ch)
        if not readings:
            raise PhraseError(
                f"无法自动推断“{ch}”的拼音，请用 --pinyin 手动指定。"
            )
        out.append(readings[0])
    return "'".join(out)


def derive_wubi(phrase: str, wubi_index: dict) -> str | None:
    """按五笔词组规则推导编码；无法推导时返回 None。"""
    chars = list(phrase)
    n = len(chars)
    char_wubi: dict[str, str] = wubi_index["char_wubi"]
    codes = [char_wubi.get(ch) for ch in chars]
    if any(c is None for c in codes):
        return None
    codes = [c for c in codes if c is not None]

    if n == 1:
        code = codes[0]
    elif n == 2:
        if len(codes[0]) < 2 or len(codes[1]) < 2:
            return None
        code = codes[0][:2] + codes[1][:2]
    elif n == 3:
        if any(len(c) < 1 for c in codes) or len(codes[2]) < 2:
            return None
        code = codes[0][0] + codes[1][0] + codes[2][:2]
    else:
        code = codes[0][0] + codes[1][0] + codes[2][0] + codes[-1][0]
    return code if WUBI_RE.match(code) else None


def resolve_entry(phrase: str, pinyin: str | None, wubi: str | None,
                  weight: int, pinyin_index: dict, wubi_index: dict) -> dict:
    """补齐拼音/五笔，返回可写入词组表的条目。"""
    phrase = validate_phrase(phrase)
    if not is_all_cjk(phrase):
        raise PhraseError(f"词组只能是汉字: {phrase!r}")

    if pinyin:
        py = segment_pinyin(pinyin, pinyin_index)
    else:
        py = auto_pinyin(phrase, pinyin_index)

    if wubi:
        wb = wubi.strip().lower()
        if not WUBI_RE.match(wb):
            raise PhraseError(f"五笔编码不合法: {wubi!r}（应为 1-4 个 a-y 字母）")
    else:
        wb = derive_wubi(phrase, wubi_index) or ""

    return {"phrase": phrase, "pinyin": py, "wubi": wb,
            "weight": validate_weight(weight)}


# ---------------------------------------------------------------------------
# 备份 / 恢复
# ---------------------------------------------------------------------------

KEEP_BACKUPS = 20


def backup_file(path: Path) -> Path | None:
    if not path.exists():
        return None
    dst = path.with_name(path.name + ".bak." + timestamp())
    shutil.copy2(path, dst)
    prune_backups(path)
    return dst


def prune_backups(path: Path) -> None:
    backups = sorted(path.parent.glob(path.name + ".bak.*"))
    for old in backups[:-KEEP_BACKUPS]:
        old.unlink(missing_ok=True)


def list_backups(path: Path) -> list[Path]:
    return sorted(path.parent.glob(path.name + ".bak.*"))


# ---------------------------------------------------------------------------
# fcitx5 进程控制
# ---------------------------------------------------------------------------

def process_control_enabled() -> bool:
    """测试/容器里可用 FCITX5_PHRASE_NO_PROCESS=1 跳过启停 fcitx5。"""
    return os.environ.get("FCITX5_PHRASE_NO_PROCESS", "").lower() not in (
        "1", "true", "yes", "on")


def fcitx_running() -> bool:
    if not process_control_enabled():
        return False
    return run(["pgrep", "-x", "fcitx5"]).returncode == 0


def stop_fcitx() -> bool:
    """停止 fcitx5；返回它之前是否在运行。"""
    if not process_control_enabled():
        return False
    if not fcitx_running():
        return False
    run(["fcitx5-remote", "-e"])
    for _ in range(20):
        if not fcitx_running():
            return True
        import time
        time.sleep(0.25)
    raise PhraseError(
        "fcitx5 未能停止。为避免它把旧词库写回、覆盖修改，本次已中止。\n"
        "请手动退出 fcitx5 后重试。"
    )


def start_fcitx() -> None:
    if not process_control_enabled():
        return
    import time
    subprocess.Popen(
        ["fcitx5", "-d"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    for _ in range(20):
        if fcitx_running():
            return
        time.sleep(0.25)


# ---------------------------------------------------------------------------
# 词库导出 / 重建 / 校验
# ---------------------------------------------------------------------------

def dump_dict(kind: str) -> list[str]:
    """导出用户词库为文本行；不存在则返回空列表。"""
    if kind == "pinyin":
        path, cmd = pinyin_dict_path(), ["libime_pinyindict", "-d"]
    elif kind == "wbpy":
        path, cmd = wbpy_dict_path(), ["libime_tabledict", "-d", "-u"]
    else:
        raise ValueError(kind)
    if not path.exists() or path.stat().st_size == 0:
        return []
    with tempfile.NamedTemporaryFile("w+", suffix=".txt", delete=False) as tf:
        tmp = Path(tf.name)
    try:
        cp = run(cmd + [str(path), str(tmp)])
        if cp.returncode != 0:
            raise PhraseError(f"导出 {kind} 词库失败: {cp.stderr.strip()}")
        return tmp.read_text(encoding="utf-8", errors="replace").splitlines()
    finally:
        tmp.unlink(missing_ok=True)


def _wbpy_header() -> str:
    """取主码表 [Data] 之前的头部，用于新建用户码表。"""
    index_cache = cache_dir() / "wbpy_header.txt"
    try:
        return index_cache.read_text(encoding="utf-8")
    except Exception:
        pass
    dump = ensure_main_dump()
    header_lines: list[str] = []
    for line in dump.read_text(encoding="utf-8", errors="replace").splitlines():
        header_lines.append(line)
        if line.strip() == "[Data]":
            break
    if not header_lines or header_lines[-1].strip() != "[Data]":
        raise PhraseError("主码表缺少 [Data] 段，无法创建用户码表")
    header = "\n".join(header_lines) + "\n"
    cache_dir().mkdir(parents=True, exist_ok=True)
    index_cache.write_text(header, encoding="utf-8")
    return header


def _compile(kind: str, text_lines: list[str], target: Path) -> None:
    """把文本编译到目标同目录的临时文件，再原子替换。

    临时目录必须和目标在同一文件系统，否则 os.replace 会报
    EXDEV (Invalid cross-device link)：/tmp 常是 tmpfs，而家目录不是。
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
            prefix=".fcitx5-phrase-", dir=str(target.parent)) as td:
        src = Path(td) / "source.txt"
        dst = Path(td) / "result.dict"
        src.write_text("\n".join(text_lines) + "\n", encoding="utf-8")
        if kind == "pinyin":
            cmd = ["libime_pinyindict", str(src), str(dst)]
        else:
            cmd = ["libime_tabledict", "-u", str(src), str(dst)]
        cp = run(cmd)
        if cp.returncode != 0 or not dst.exists() or dst.stat().st_size == 0:
            raise PhraseError(
                f"编译 {kind} 词库失败: {cp.stderr.strip() or '未知错误'}"
            )
        os.chmod(dst, 0o600)
        os.replace(dst, target)


def _verify(kind: str, expected: set[str]) -> None:
    actual = set(dump_dict(kind))
    missing = expected - actual
    if missing:
        sample = "\n".join("  " + m for m in sorted(missing)[:10])
        raise PhraseError(
            f"{kind} 词库校验失败，以下词条未写入:\n{sample}"
        )


def rebuild_pinyin(entries: list[dict],
                   remove_phrases: set[str] | None = None) -> None:
    target = pinyin_dict_path()
    remove = set(remove_phrases or ())
    current = {e["phrase"] for e in entries if e.get("pinyin")}
    phrases = current | remove
    if not phrases:
        return
    kept: list[str] = []
    for line in dump_dict("pinyin"):
        parts = line.split()
        if len(parts) >= 2 and parts[0] in phrases:
            continue
        kept.append(line)
    new_lines = [
        f"{e['phrase']} {e['pinyin']} {e['weight']}"
        for e in entries if e.get("pinyin")
    ]
    if not new_lines and not target.exists():
        return
    expected = set(new_lines)
    backup = backup_file(target)
    try:
        _compile("pinyin", kept + new_lines, target)
        _verify("pinyin", expected)
    except Exception:
        if backup:
            shutil.copy2(backup, target)
        raise


def _wbpy_data_lines(entries: list[dict],
                     remove_phrases: set[str] | None = None,
                     ) -> tuple[list[str], set[str]]:
    remove = set(remove_phrases or ())
    phrases = {e["phrase"] for e in entries if e.get("wubi")} | remove
    pairs = {(e["wubi"], e["phrase"]) for e in entries if e.get("wubi")}

    kept: list[str] = []
    auto: list[str] = []
    in_auto = False
    for line in dump_dict("wbpy"):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped == "[Auto]":
            in_auto = True
            continue
        if stripped.startswith("["):
            continue
        if in_auto:
            # [Auto] 段的格式是 "编码 词组 词频"；libime 重建会丢弃整段，
            # 这里转成普通词条 "编码 词组" 保留下来，避免每次 apply 都丢学习成果。
            parts = stripped.split()
            if len(parts) >= 2 and parts[1] not in phrases:
                auto.append(f"{parts[0]} {parts[1]}")
            continue
        code, _, phrase = stripped.partition(" ")
        if phrase in phrases or (code, phrase) in pairs:
            continue
        kept.append(line)

    new_lines = [
        f"{e['wubi']} {e['phrase']}"
        for e in entries if e.get("wubi")
    ]
    # 去重：显式词条 > 自动学习 > 原有词条
    result: list[str] = []
    seen: set[tuple[str, str]] = set()
    for line in new_lines + auto + kept:
        code, _, phrase = line.partition(" ")
        key = (code, phrase)
        if key in seen:
            continue
        seen.add(key)
        result.append(line)
    return result, set(new_lines)


def rebuild_wbpy(entries: list[dict],
                 remove_phrases: set[str] | None = None) -> None:
    target = wbpy_dict_path()
    remove = set(remove_phrases or ())
    if not any(e.get("wubi") for e in entries) and not remove:
        return
    body, expected = _wbpy_data_lines(entries, remove)
    if not expected and not target.exists():
        return
    text = [_wbpy_header().rstrip("\n")] + body
    backup = backup_file(target)
    try:
        _compile("wbpy", text, target)
        _verify("wbpy", expected)
    except Exception:
        if backup:
            shutil.copy2(backup, target)
        raise


def apply_entries(entries: list[dict], *, remove_phrases: set[str] | None = None,
                  restart: bool = True, verbose: bool = True) -> None:
    require_tools()
    current = {e["phrase"] for e in entries}
    # 上次管理过、但这次已从词组表删掉的词，也要从二进制词库移除
    to_remove = set(remove_phrases or ()) | (read_managed() - current)
    if not entries and not to_remove:
        raise PhraseError("词组表为空，没有可应用的词条")
    was_running = stop_fcitx()
    try:
        if verbose:
            print("重建拼音用户词库 ...")
        rebuild_pinyin(entries, to_remove)
        if verbose:
            print("重建五笔拼音用户码表 ...")
        rebuild_wbpy(entries, to_remove)
        write_managed(current)
    finally:
        if was_running and restart:
            if verbose:
                print("重新启动 fcitx5 ...")
            start_fcitx()


# ---------------------------------------------------------------------------
# 可复用的操作（CLI 与 Web 共用）
# ---------------------------------------------------------------------------

_INDEX_MEMORY: tuple[dict, dict] | None = None


def get_indexes() -> tuple[dict, dict]:
    """进程内缓存索引；Web 服务长期运行时不必每次重读码表。"""
    global _INDEX_MEMORY
    if _INDEX_MEMORY is None:
        _INDEX_MEMORY = build_indexes()
    return _INDEX_MEMORY


def perform_add(phrase: str, pinyin: str | None, wubi: str | None,
                weight: int, *, apply: bool) -> tuple[dict, int]:
    pinyin_index, wubi_index = get_indexes()
    entry = resolve_entry(phrase, pinyin, wubi, weight,
                          pinyin_index, wubi_index)
    entries = read_phrase_file()
    upsert(entries, entry)
    write_phrase_file(entries)
    if apply:
        apply_entries(entries, verbose=False)
    return entry, len(entries)


def perform_remove(phrase: str, *, apply: bool) -> int:
    entries = read_phrase_file()
    phrase = validate_phrase(phrase)
    kept = [e for e in entries if e["phrase"] != phrase]
    if len(kept) == len(entries):
        raise PhraseError(f"词组表里没有: {phrase}")
    write_phrase_file(kept)
    if apply:
        apply_entries(kept, remove_phrases={phrase}, verbose=False)
    return len(kept)


def parse_import_text(text: str, *, delimiter: str | None = None) -> list[dict]:
    pinyin_index, wubi_index = get_indexes()
    imported: list[dict] = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if delimiter:
            parts = [p.strip() for p in raw.split(delimiter)]
        elif "\t" in raw:
            parts = [p.strip() for p in raw.split("\t")]
        elif "," in raw:
            parts = [p.strip() for p in raw.split(",")]
        else:
            parts = raw.split()
        while len(parts) < 4:
            parts.append("")
        try:
            e = resolve_entry(parts[0], parts[1] or None, parts[2] or None,
                              int(parts[3]) if parts[3] else 100,
                              pinyin_index, wubi_index)
        except PhraseError as exc:
            raise PhraseError(f"第 {lineno} 行: {exc}")
        imported.append(e)
    return imported


def perform_import(text: str, *, replace: bool = False,
                   delimiter: str | None = None,
                   apply: bool) -> tuple[int, int]:
    imported = parse_import_text(text, delimiter=delimiter)
    if not imported:
        raise PhraseError("没有可导入的词条")
    entries = [] if replace else read_phrase_file()
    for e in imported:
        upsert(entries, e)
    write_phrase_file(entries)
    if apply:
        apply_entries(entries, verbose=False)
    return len(imported), len(entries)


def export_text() -> str:
    entries = read_phrase_file()
    lines = [
        "# fcitx5-custom-phrase 词组表",
        "# 格式: 词组<TAB>拼音<TAB>五笔编码<TAB>权重",
    ]
    lines += [
        "\t".join([e["phrase"], e["pinyin"], e["wubi"], str(e["weight"])])
        for e in entries
    ]
    return "\n".join(lines) + ("\n" if entries else "")


def state_info() -> dict:
    entries = read_phrase_file()
    return {
        "version": VERSION,
        "entries": entries,
        "count": len(entries),
        "fcitx_running": fcitx_running(),
        "config_path": str(config_path()),
        "pinyin_dict": str(pinyin_dict_path()),
        "wbpy_dict": str(wbpy_dict_path()),
        "main_dict": str(main_dict_path()),
    }


# ---------------------------------------------------------------------------
# 命令
# ---------------------------------------------------------------------------

def cmd_add(args: argparse.Namespace) -> int:
    entry, total = perform_add(
        args.phrase, args.pinyin, args.wubi, args.weight,
        apply=not args.no_apply,
    )
    print(f"已写入词组表: {entry['phrase']}  "
          f"拼音={entry['pinyin']}  五笔={entry['wubi'] or '(无)'}  "
          f"权重={entry['weight']}")
    if args.no_apply:
        print("未应用。运行 `fcitx5-phrase apply` 使其生效。")
    else:
        print("完成，输入编码即可看到新词组。")
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    total = perform_remove(args.phrase, apply=not args.no_apply)
    print(f"已从词组表移除: {args.phrase}")
    if args.no_apply:
        print("未应用。运行 `fcitx5-phrase apply` 使其生效。")
    elif total == 0:
        print("词组表已空。")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    entries = read_phrase_file()
    if args.json:
        print(json.dumps(entries, ensure_ascii=False, indent=2))
        return 0
    if not entries:
        print(f"（词组表为空: {config_path()}）")
        return 0
    headers = ("词组", "拼音", "五笔", "权重")
    rows = [(e["phrase"], e["pinyin"], e["wubi"] or "-", str(e["weight"]))
            for e in entries]

    def width(s: str) -> int:
        return sum(2 if is_cjk(c) else 1 for c in s)

    widths = [max(width(h), *(width(r[i]) for r in rows))
              for i, h in enumerate(headers)]
    fmt = "  ".join("{:<%d}" % w for w in widths)
    print(fmt.format(*headers))
    print("  ".join("-" * w for w in widths))
    for r in rows:
        print(fmt.format(*r))
    print(f"\n共 {len(entries)} 条   {config_path()}")
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    src = Path(args.file).expanduser()
    if not src.exists():
        raise PhraseError(f"文件不存在: {src}")
    imported, total = perform_import(
        src.read_text(encoding="utf-8"),
        replace=args.replace, delimiter=args.delimiter,
        apply=not args.no_apply,
    )
    print(f"已导入 {imported} 条，词组表共 {total} 条。")
    if args.no_apply:
        print("未应用。运行 `fcitx5-phrase apply` 使其生效。")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    text = export_text()
    if args.file:
        Path(args.file).expanduser().write_text(text, encoding="utf-8")
        print(f"已导出 {len(read_phrase_file())} 条到 {args.file}")
    else:
        sys.stdout.write(text)
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    entries = read_phrase_file()
    apply_entries(entries, restart=not args.no_restart)
    print(f"已应用 {len(entries)} 条自定义词组。")
    return 0


def cmd_dump(args: argparse.Namespace) -> int:
    path = pinyin_dict_path() if args.target == "pinyin" else wbpy_dict_path()
    lines = dump_dict(args.target)
    if not lines:
        print("（用户词库不存在）" if not path.exists() else "（用户词库为空）")
    else:
        print("\n".join(lines))
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    targets = {
        "pinyin": pinyin_dict_path(),
        "wbpy": wbpy_dict_path(),
    }
    if args.target != "all":
        targets = {args.target: targets[args.target]}
    if args.list:
        for name, path in targets.items():
            print(f"[{name}] {path}")
            for b in list_backups(path):
                print("  " + b.name)
        return 0
    was_running = stop_fcitx()
    try:
        for name, path in targets.items():
            backups = list_backups(path)
            if not backups:
                print(f"[{name}] 没有备份，跳过")
                continue
            shutil.copy2(backups[-1], path)
            os.chmod(path, 0o600)
            print(f"[{name}] 已恢复 {backups[-1].name}")
    finally:
        if was_running:
            start_fcitx()
    return 0


def cmd_web(args: argparse.Namespace) -> int:
    from fcitx5_phrase_web import run_web
    return run_web(host=args.host, port=args.port, token=args.token,
                   open_browser=args.open_browser)


def cmd_doctor(args: argparse.Namespace) -> int:
    ok = True
    print(f"fcitx5-phrase {VERSION}")
    print(f"Python: {sys.version.split()[0]}")
    if os.geteuid() == 0:
        print("!! 正在以 root 运行：词库在用户家目录，请用普通用户运行")
        ok = False
    print(f"词组表: {config_path()}  "
          f"({'存在' if config_path().exists() else '不存在'})")
    try:
        print(f"  词条数: {len(read_phrase_file())}")
    except PhraseError as exc:
        print(f"  !! {exc}")
        ok = False
    for tool in ("libime_pinyindict", "libime_tabledict", "fcitx5", "fcitx5-remote"):
        path = shutil.which(tool)
        print(f"{tool}: {path or '缺失'}")
        if path is None and tool.startswith("libime"):
            ok = False
    print(f"主码表: {main_dict_path()}  "
          f"({'存在' if main_dict_path().exists() else '缺失'})")
    if not main_dict_path().exists():
        ok = False
    print(f"拼音词库: {pinyin_dict_path()}")
    print(f"五笔词库: {wbpy_dict_path()}")
    print(f"fcitx5 运行中: {'是' if fcitx_running() else '否'}")
    if main_dict_path().exists():
        try:
            pidx, widx = build_indexes()
            print(f"音节数: {len(pidx['syllables'])}  "
                  f"五笔单字: {len(widx['char_wubi'])}")
        except PhraseError as exc:
            print(f"!! 索引构建失败: {exc}")
            ok = False
    print("\n" + ("一切正常。" if ok else "存在问题，请按上面提示处理。"))
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fcitx5-phrase",
        description="fcitx5 拼音 / 五笔拼音自定义词组管理器",
    )
    parser.add_argument("--version", action="version",
                        version=f"%(prog)s {VERSION}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("add", help="添加/更新一条词组并应用")
    p.add_argument("phrase", help="词组，如 微信")
    p.add_argument("pinyin", nargs="?", help="拼音，可连写 weixin；留空自动推断")
    p.add_argument("-w", "--wubi", help="五笔编码，如 tmwy；留空自动推断")
    p.add_argument("-W", "--weight", type=int, default=100, help="权重，默认 100")
    p.add_argument("--no-apply", action="store_true", help="只写词组表，不重建词库")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("remove", help="删除一条词组并应用")
    p.add_argument("phrase")
    p.add_argument("--no-apply", action="store_true")
    p.set_defaults(func=cmd_remove)

    p = sub.add_parser("list", help="列出词组表")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("import", help="从 TSV/CSV 批量导入")
    p.add_argument("file")
    p.add_argument("--replace", action="store_true", help="替换而不是合并")
    p.add_argument("--delimiter", help="显式指定分隔符")
    p.add_argument("--no-apply", action="store_true")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("export", help="导出词组表")
    p.add_argument("file", nargs="?")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("apply", help="按词组表重建用户词库")
    p.add_argument("--no-restart", action="store_true",
                   help="不要在结束后重启 fcitx5（测试用）")
    p.set_defaults(func=cmd_apply)

    p = sub.add_parser("dump", help="导出当前二进制用户词库")
    p.add_argument("target", choices=["pinyin", "wbpy"])
    p.set_defaults(func=cmd_dump)

    p = sub.add_parser("restore", help="从备份恢复用户词库")
    p.add_argument("target", nargs="?", default="all",
                   choices=["all", "pinyin", "wbpy"])
    p.add_argument("--list", action="store_true", help="只列出备份")
    p.set_defaults(func=cmd_restore)

    p = sub.add_parser("web", help="启动图形化 Web 管理界面")
    p.add_argument("--host", default="127.0.0.1",
                   help="监听地址，默认仅本机 127.0.0.1")
    p.add_argument("--port", type=int, default=8765, help="端口，默认 8765")
    p.add_argument("--token", default=None, help="固定 CSRF token（测试用）")
    p.add_argument("--open", dest="open_browser", action="store_true",
                   default=True, help="自动打开浏览器（默认）")
    p.add_argument("--no-open", dest="open_browser", action="store_false",
                   help="不自动打开浏览器")
    p.set_defaults(func=cmd_web)

    p = sub.add_parser("doctor", help="检查环境")
    p.set_defaults(func=cmd_doctor)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except PhraseError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"系统错误: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n已取消", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
