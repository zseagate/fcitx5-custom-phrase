#!/bin/bash
# 给 fcitx5 拼音 + 五笔拼音添加自定义词组
# 注意: 必须以普通用户运行, 不要加 sudo (否则会改到 /root 下的文件)
#
# 用法: ./add_phrase.sh <拼音> <五笔编码> <词组> [权重]
# 示例: ./add_phrase.sh "wei'xin" tmwy 微信 100
#   拼音必须用 ' 分隔音节 (libime 格式), 如 wei'xin, teng'xun'wei'xin

set -u

if [ "$(id -u)" = "0" ]; then
    echo "错误: 不要用 sudo 运行本脚本, 直接以普通用户运行即可" >&2
    exit 1
fi

for cmd in libime_pinyindict libime_tabledict; do
    command -v "$cmd" >/dev/null 2>&1 || { echo "错误: 找不到 $cmd" >&2; exit 1; }
done

PINYIN_USER_DICT="$HOME/.local/share/fcitx5/pinyin/user.dict"
WBPY_USER_DICT="$HOME/.local/share/fcitx5/table/wbpy.user.dict"

backup_file() {
    local f="$1"
    [ -f "$f" ] && cp "$f" "$f.bak.$(date +%Y%m%d%H%M%S)"
}

add_to_pinyin() {
    local pinyin="$1" phrase="$2" weight="${3:-100}"
    local tmp_txt="/tmp/pinyin_user_dict_$$.txt"
    local err="/tmp/pinyin_err_$$.log"

    echo "正在添加拼音词组: $phrase <- $pinyin (权重: $weight)"
    backup_file "$PINYIN_USER_DICT"
    if [ -f "$PINYIN_USER_DICT" ]; then
        libime_pinyindict -d "$PINYIN_USER_DICT" "$tmp_txt" 2>"$err" || { echo "导出拼音词库失败"; cat "$err"; rm -f "$tmp_txt" "$err"; return 1; }
    else
        : > "$tmp_txt"
    fi

    # libime 拼音文本格式: "词组 拼音 词频", 拼音音节用 ' 分隔
    if grep -qxF "$phrase $pinyin $weight" "$tmp_txt" 2>/dev/null; then
        echo "拼音词组 '$phrase' 已存在, 跳过"
        rm -f "$tmp_txt" "$err"
        return 0
    fi
    printf '%s %s %s\n' "$phrase" "$pinyin" "$weight" >> "$tmp_txt"

    if ! libime_pinyindict "$tmp_txt" "$PINYIN_USER_DICT" 2>"$err"; then
        echo "拼音词库写入失败:"; cat "$err"; rm -f "$tmp_txt" "$err"; return 1
    fi
    chmod 600 "$PINYIN_USER_DICT"

    # 真校验: 重新导出, 确认词条确实在里面 (工具退出码不可信, 解析失败也返回 0)
    local verify="/tmp/pinyin_verify_$$.txt"
    libime_pinyindict -d "$PINYIN_USER_DICT" "$verify" 2>/dev/null
    if grep -qxF "$phrase $pinyin $weight" "$verify" 2>/dev/null; then
        echo "拼音词组添加成功 (已校验)"
    else
        echo "拼音词组添加失败: 词条未写入, 常见原因是拼音格式不对 (要用 ' 分隔音节, 如 wei'xin)"
        echo "--- 工具报错:"; cat "$err"
        rm -f "$tmp_txt" "$err" "$verify"
        return 1
    fi
    rm -f "$tmp_txt" "$err" "$verify"
}

add_to_wbpy() {
    local code="$1" phrase="$2"
    local tmp_txt="/tmp/wbpy_user_dict_$$.txt"
    local err="/tmp/wbpy_err_$$.log"

    echo "正在添加五笔拼音词组: $phrase <- $code"
    [ -f "$WBPY_USER_DICT" ] || { echo "错误: 找不到 $WBPY_USER_DICT" >&2; return 1; }
    backup_file "$WBPY_USER_DICT"
    libime_tabledict -d -u "$WBPY_USER_DICT" "$tmp_txt" 2>"$err" || { echo "导出五笔词库失败"; cat "$err"; rm -f "$tmp_txt" "$err"; return 1; }

    # 码表文本格式: "编码 词组" (无权重列); 精确到 编码+词组 去重
    if grep -qxF "$code $phrase" "$tmp_txt" 2>/dev/null; then
        echo "五笔拼音词组 '$phrase' 已存在, 跳过"
        rm -f "$tmp_txt" "$err"
        return 0
    fi

    # 插到 [Auto] 段之前 (工具重建时会丢弃 [Auto] 段之后的内容);
    # 若同编码已存在, 插到它前面以排第一候选
    awk -v line="$code $phrase" -v code="$code" '
        /^\[Auto\]$/ && !done { print line; done=1 }
        $1 == code && !done { print line; done=1 }
        { print }
        END { if (!done) print line }
    ' "$tmp_txt" > "$tmp_txt.new" && mv "$tmp_txt.new" "$tmp_txt"

    if ! libime_tabledict -u "$tmp_txt" "$WBPY_USER_DICT" 2>"$err"; then
        echo "五笔词库写入失败 (工具崩溃), 已保留备份, 原文件未动:"; cat "$err"
        rm -f "$tmp_txt" "$err"
        return 1
    fi
    chmod 600 "$WBPY_USER_DICT"

    local verify="/tmp/wbpy_verify_$$.txt"
    libime_tabledict -d -u "$WBPY_USER_DICT" "$verify" 2>/dev/null
    if grep -qxF "$code $phrase" "$verify" 2>/dev/null; then
        echo "五笔拼音词组添加成功 (已校验)"
    else
        echo "五笔拼音词组添加失败: 词条未写入"; cat "$err"
        rm -f "$tmp_txt" "$err" "$verify"
        return 1
    fi
    rm -f "$tmp_txt" "$err" "$verify"
}

fcitx_was_running=0
if pgrep -x fcitx5 >/dev/null 2>&1; then
    fcitx_was_running=1
    echo "正在停止 fcitx5 (防止它覆盖词库文件)..."
    fcitx5-remote -e 2>/dev/null
    for _ in $(seq 1 20); do pgrep -x fcitx5 >/dev/null || break; sleep 0.5; done
    if pgrep -x fcitx5 >/dev/null 2>&1; then
        echo "错误: fcitx5 未能停止, 为避免词库被覆盖, 本次不做修改" >&2
        exit 1
    fi
fi

start_fcitx5() {
    if [ "$fcitx_was_running" = "1" ]; then
        echo "正在重新启动 fcitx5..."
        fcitx5 -d >/dev/null 2>&1
        sleep 2
        pgrep -x fcitx5 >/dev/null 2>&1 && echo "fcitx5 已启动" || echo "警告: fcitx5 启动失败, 请手动运行 fcitx5 -d" >&2
    fi
}

if [ "$#" -lt 3 ]; then
    start_fcitx5
    echo "用法: $0 <拼音> <五笔编码> <词组> [权重]"
    echo "示例: $0 \"wei'xin\" tmwy 微信 100"
    exit 1
fi

add_to_pinyin "$1" "$3" "${4:-100}"
add_to_wbpy "$2" "$3"

start_fcitx5

echo ""
echo "完成, 现在输入编码即可看到新词组"
echo "注意: 五笔用户词库重建会丢弃 [Auto] 自动学习段 (用久了会自动重新学习); 原文件已备份为 *.bak.<时间>"
