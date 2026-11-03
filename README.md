# fcitx5-custom-phrase

给 fcitx5 的**拼音**和**五笔拼音**添加、管理与同步自定义词组的工具。

`fcitx5` 的用户词库是二进制格式，不能直接编辑。本工具以一份**纯文本词组表**为唯一数据源，
自动完成「导出 → 合并 → 重建二进制 → 重新导出校验 → 重启 fcitx5」的全流程。

> 旧版只有 `add_phrase.sh`：一次只能加一条，拼音必须手动用 `'` 分段，五笔码必须手填，
> 而且首次没有 `wbpy.user.dict` 时会直接失败。新版保留了对旧脚本的兼容。

## 图形界面（推荐，不用终端）

安装一次应用菜单项：

```bash
./install-web-launcher.sh
```

然后在应用菜单里搜索 **「Fcitx5 自定义词组」** 打开即可。
也可以直接运行：

```bash
./fcitx5-phrase web --open
```

浏览器会打开 `http://127.0.0.1:8765/`，可以：

- 添加 / 编辑 / 删除词组
- 搜索、按拼音或五笔编码过滤
- 批量导入（Tab / 逗号 / 空格分隔）、一键导出
- 「应用全部」重新生成词库
- 「退出服务」关闭后台服务

> 界面只监听本机 `127.0.0.1`，并用随机 CSRF token 保护，不会暴露到局域网。

## 特性

- **纯文本为源**：词组表 `~/.config/fcitx5/custom-phrase.tsv` 可读、可备份、可进 git。
- **拼音自动分段**：`weixin` → `wei'xin`，不必手动敲 `'`。
- **拼音自动推断**：不写拼音也能加，多音字优先用主码表里的**整词读音**。
- **五笔自动推断**：按主码表的五笔词组取码规则自动生成编码。
- **增删改查**：`add` / `remove` / `list` / `import` / `export`。
- **删除会同步**：记录上次管理的词条，从词组表删掉的词也会从二进制词库移除。
- **安全**：先停 fcitx5、写临时文件后原子替换、重建后重新导出校验、失败自动回滚、备份轮转。
- **批量执行**：一次 `apply` 处理全部词条，只启停 fcitx5 一次。
- **兼容旧版**：`add_phrase.sh <拼音> <五笔> <词组> [权重]` 参数顺序不变。

## 环境要求

- fcitx5（`pinyin` 和/或 `table` 输入法）
- `libime_pinyindict`、`libime_tabledict`（来自 `libime-bin`，通常随 fcitx5 安装）
- 五笔拼音主码表 `/usr/share/libime/wbpy.main.dict`（来自 `libime-data`）
- Python 3.8+

## 安装

```bash
git clone https://github.com/zseagate/fcitx5-custom-phrase.git
cd fcitx5-custom-phrase
chmod +x fcitx5-phrase add_phrase.sh
```

可选：放进 `PATH`：

```bash
mkdir -p ~/.local/bin
ln -sf "$PWD/fcitx5-phrase" ~/.local/bin/fcitx5-phrase
```

## 快速开始

```bash
# 拼音可连写，五笔码可省略
./fcitx5-phrase add 微信 weixin

# 拼音和五笔都自动推断
./fcitx5-phrase add 王者荣耀

# 只写词组表，暂不重建词库
./fcitx5-phrase add 内卷 neijuan --no-apply

# 让词组表生效（会自动停/启 fcitx5）
./fcitx5-phrase apply

# 查看 / 删除
./fcitx5-phrase list
./fcitx5-phrase remove 王者荣耀

# 批量导入 / 导出
./fcitx5-phrase import my-phrases.tsv
./fcitx5-phrase export backup.tsv

# 检查环境
./fcitx5-phrase doctor
```

加完后，拼音下输入 `weixin`、五笔拼音下输入 `tmwy`，即可在候选里看到新词。

## 命令

| 命令 | 说明 |
| --- | --- |
| `add <词组> [拼音] [-w 五笔] [-W 权重] [--no-apply]` | 添加或更新一条，默认立即 `apply` |
| `remove <词组> [--no-apply]` | 从词组表删除，并同步从二进制词库移除 |
| `list [--json]` | 列出词组表 |
| `import <文件> [--replace] [--delimiter X] [--no-apply]` | 从 TSV/CSV 批量导入 |
| `export [文件]` | 导出词组表（省略文件则输出到 stdout） |
| `apply [--no-restart]` | 按词组表重建用户词库 |
| `dump pinyin\|wbpy` | 查看当前二进制用户词库内容 |
| `restore [pinyin\|wbpy\|all] [--list]` | 从最近备份恢复 |
| `web [--port 8765] [--open]` | 启动图形化 Web 管理界面 |
| `doctor` | 检查依赖、路径、码表与运行状态 |

## 词组表格式

默认路径：`~/.config/fcitx5/custom-phrase.tsv`

以 Tab 分隔，`#` 开头为注释：

```tsv
# 词组	拼音	五笔编码	权重
微信	wei'xin	tmwy	100
重庆	chong'qing	tgyd	100
王者荣耀	wang'zhe'rong'yao	wyrl	100
```

- 拼音可写连写形式（`weixin`），工具会规范成 `wei'xin`；也可直接写 `'` 分隔形式。
- 拼音留空 → 自动推断。
- 五笔留空 → 自动推断；推断失败则只加到拼音词库。
- 权重只影响拼音候选排序，默认 100，越大越靠前。
- 同一词组重复出现时，后一条覆盖前一条。

## 自动推断说明

### 拼音

1. 如果词组在主码表的 `@` 拼音段里存在整词读音，优先使用它（可正确处理“重要 / 银行 / 西安”）。
2. 否则逐字取码表里出现频率最高的读音。
3. 连写拼音会用 DP 切成合法音节；`xian` 默认切成 `xian`（现），
   要表达“西安”请写 `xi'an`。

多音字仍有歧义（例如“长”），自动结果不满意时手动指定即可：

```bash
./fcitx5-phrase add 重要 zhongyao
```

### 五笔

按五笔词组规则，从单字全码推导：

| 词长 | 规则 | 示例 |
| --- | --- | --- |
| 2 字 | 每字前两码 | 微信 `tmgt`+`wyg` → `tmwy` |
| 3 字 | 前两字首码 + 第三字前两码 | — |
| ≥4 字 | 前三字首码 + 末字首码 | 自定义词组 → `tpyx` |

推导不出来（生僻字/码表缺失）时，五笔部分留空，不影响拼音词库。

## 工作原理

1. 读取词组表；缺拼音/五笔的条目先补齐。
2. 停掉正在运行的 fcitx5（避免它把内存里的旧词库写回，覆盖修改）。
3. 导出两个用户词库为文本：
   - `~/.local/share/fcitx5/pinyin/user.dict`
   - `~/.local/share/fcitx5/table/wbpy.user.dict`
4. 移除「当前词条 + 上次管理但这次已删除」的旧行，追加新词条。
   五笔码表的 `[Auto]` 自动学习段原本会被 libime 重建丢弃，本工具会把它
   转成普通词条保留，避免每次 `apply` 都丢掉学习成果。
5. 在目标词库的**同一目录/同一文件系统**编译临时文件，再**原子替换**
   （避免 `/tmp` 为 tmpfs 时出现 `EXDEV` 跨设备错误）。
6. **重新导出校验**（libime 工具解析失败也可能返回 0，不能只看退出码）。
7. 校验失败 → 自动回滚本次备份；成功 → 记录本次管理的词条。
8. 重新启动 fcitx5（如果它之前正在运行）。

## 安全与回滚

- 每次重建前备份为 `*.bak.<时间戳>`，默认保留最近 20 份。
- 编译失败或校验失败会自动还原本次备份。
- 想手动恢复：

```bash
./fcitx5-phrase restore --list     # 列出备份
./fcitx5-phrase restore all        # 恢复两个词库的最新备份
./fcitx5-phrase restore wbpy       # 只恢复五笔词库
```

## 从旧版迁移

旧命令仍可用：

```bash
./add_phrase.sh "wei'xin" tmwy 微信 100
```

它等价于：

```bash
./fcitx5-phrase add 微信 "wei'xin" --wubi tmwy --weight 100
```

旧版没有词组表，迁移后第一次 `add/apply` 会生成 `custom-phrase.tsv`；
之前直接写进二进制词库的词不会被自动纳入管理，但也不会被删除。

## 故障排查

| 现象 | 原因 / 解法 |
| --- | --- |
| `找不到 libime 工具` | `sudo apt install libime-bin` |
| `找不到五笔拼音主码表` | `sudo apt install libime-data` |
| 提示拼音无法切分 | 手动用 `'` 分隔，如 `xi'an`、`fang'an` |
| 加完打不出来 | 确认 fcitx5 已重启；`./fcitx5-phrase doctor`；五笔重码时按序号选一次会学习提前 |
| 删除后还在 | 确认执行了 `apply`；`./fcitx5-phrase dump wbpy` 查看实际词库 |
| fcitx5 停不下来 | 脚本会中止以避免覆盖；手动退出 fcitx5 后重试 |
| 想临时不启停 fcitx5（测试/容器） | 设置 `FCITX5_PHRASE_NO_PROCESS=1` |

## 开发与测试

```bash
python3 -m unittest discover -s tests -v
```

测试使用临时 `HOME`，不会修改真实词库。

主要文件：

- `fcitx5-phrase`：命令行入口
- `fcitx5_phrase.py`：核心实现（词库 / 拼音 / 五笔 / CLI）
- `fcitx5_phrase_web.py`：Web 界面与 HTTP 服务
- `add_phrase.sh`：旧版兼容脚本
- `install-web-launcher.sh`：安装应用菜单项
- `tests/test_core.py`：单元 + 集成测试
