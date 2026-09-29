# fcitx5-custom-phrase

给 fcitx5 的**拼音**和**五笔拼音**用户词库添加自定义词组的小脚本。

背景：`fcitx5` 的用户词库是二进制格式，不能直接编辑。本脚本用 `libime` 自带的
词典工具做 文本导出 → 追加词条 → 重建二进制 → 校验 → 重启 fcitx5 全流程。

## 环境要求

- fcitx5（`table` / `pinyin` 输入法）
- `libime_pinyindict`、`libime_tabledict`（一般随 fcitx5 一起安装）

## 用法

```bash
./add_phrase.sh "<拼音>" <五笔编码> <词组> [权重]
```

- `<拼音>`：libime 格式，**词组在前、音节用 `'` 分隔**，如 `"wei'xin"`、`"teng'xun'wei'xin"`
- `<五笔编码>`：如 `tmwy`
- `<词组>`：如 `微信`
- `[权重]`：只对拼音有效，默认 `100`，越大排名越靠前

示例（拼音 + 五笔拼音同时添加“微信”）：

```bash
./add_phrase.sh "wei'xin" tmwy 微信 100
```

成功后直接生效：拼音下输 `weixin`，五笔拼音下输 `tmwy` 即可看到“微信”
（五笔若与生僻字重码，按候选序号选择一次后会自动学习提前）。

## 脚本做了什么

1. 拒绝 `sudo` 运行（词库在用户家目录下，用 root 会写错地方）。
2. 备份两个用户词库（`*.bak.时间戳`，和原文件放一起）：
   - `~/.local/share/fcitx5/pinyin/user.dict`
   - `~/.local/share/fcitx5/table/wbpy.user.dict`
3. 导出为文本，追加词条后重建二进制。
4. **重新导出校验**（`libime` 工具解析失败也返回 0，只看退出码不可信）。
5. 先停 fcitx5 再改文件、改完再启动（见下）。

## 已知的坑（都已在脚本里处理）

- **拼音文本格式是“词组 拼音 词频”**，且拼音音节必须用 `'` 分隔。
  写成 `weixin 微信 100` 会被静默跳过：`Failed to parse line ... skipping`，
  但退出码依然是 0。
- **五笔码表文本格式是“编码 词组”**，没有权重列，写权重会被当成词组的一部分。
- **改五笔用户词库前必须先停 fcitx5**。fcitx5 运行时在内存里 hold 着旧词库，
  `fcitx5-remote -r` 重载配置并不会重读该文件，之后它一切换/学习就会按内存
  旧版存盘，把你的修改覆盖掉。
- **重建五笔用户词库会丢弃 `[Auto]` 自动学习段**（工具行为）。丢掉的不用担心，
  正常使用几天会自动重新学习回来；原文件有备份，可随时恢复。
- 同一编码可对应多个词（如 `tmwy` 同时有“微信”和“笍”），脚本把新词插到
  同编码旧词前面，尽量让它排第一候选。

## 故障排查

| 现象 | 原因 / 解法 |
| --- | --- |
| 脚本拒绝运行，提示不要用 sudo | 直接以普通用户运行，不要加 `sudo` |
| 提示“拼音格式不对” | 拼音改用 `' ` 分隔音节，如 `"wei'xin"` |
| 工具崩溃（core dumped） | 旧版本脚本直接覆盖了正在使用的词库文件；新版已先停 fcitx5 再操作 |
| 加完还是打不出来 | 确认 fcitx5 已重启（脚本自动处理）；五笔重码时按序号选一次即可 |

## 恢复备份

```bash
cp ~/.local/share/fcitx5/table/wbpy.user.dict.bak.<时间> ~/.local/share/fcitx5/table/wbpy.user.dict
cp ~/.local/share/fcitx5/pinyin/user.dict.bak.<时间> ~/.local/share/fcitx5/pinyin/user.dict
fcitx5 -r
```
