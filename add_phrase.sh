#!/bin/bash
# 兼容旧版用法（参数顺序保持不变）:
#   ./add_phrase.sh "<拼音>" <五笔编码> <词组> [权重]
# 例: ./add_phrase.sh "wei'xin" tmwy 微信 100
#
# 新版推荐直接使用:
#   ./fcitx5-phrase add 微信 weixin --wubi tmwy
#   ./fcitx5-phrase add 微信            # 拼音/五笔都自动推断
#
# 旧版参数顺序: pinyin wubi phrase [weight]
# 因为旧版必须同时提供拼音和五笔，这里原样转成新命令。

set -u

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ "$#" -lt 3 ]; then
    echo "用法: $0 <拼音> <五笔编码> <词组> [权重]" >&2
    echo "示例: $0 \"wei'xin\" tmwy 微信 100" >&2
    echo "" >&2
    echo "新版用法（推荐）:" >&2
    echo "  $DIR/fcitx5-phrase add 微信 weixin --wubi tmwy" >&2
    echo "  $DIR/fcitx5-phrase add 微信" >&2
    exit 1
fi

exec "$DIR/fcitx5-phrase" add "$3" "$1" --wubi "$2" --weight "${4:-100}"
