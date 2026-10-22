# Fish 行内历史补全

[English](README.md)

按词前缀查找历史，允许跳过中间参数。例如 `pod c e m` 可以匹配 `podman compose exec -it mycontainer /bin/sh`。整条命令在行内预览，只补全到最后一个匹配词，方便继续输入或接受整条命令。

## 安装

需要 Fish 4.0+ 和 `seq`。将完整的 [history_word_prefix.fish](history_word_prefix.fish) 放入 Fish 配置目录的 `conf.d/`，在 `config.fish` 中加入：

```fish
if status is-interactive
    history_word_prefix_inline_bindings
end
```

新开 Fish 后生效。也可以在脚本所在目录临时加载：

```fish
source ./history_word_prefix.fish
history_word_prefix_inline_bindings
```

默认到边界停止；使用 `history_word_prefix_inline_bindings --cycle` 开启双向循环，原输入也在循环内。循环开启时，最开始按 ↓ 会跳到最旧匹配。重复调用绑定函数可切换设置，`--help` 查看帮助。

## 使用

输入 `pod c` 后按 ↑，可能得到以下预览，`▏` 是光标，右侧是高亮尾部：

```text
podman compose ▏logs
```

继续输入 `e` 会得到 `podman compose e▏`，再按 ↑ 可匹配到 `podman compose exec ▏-it mycontainer /bin/sh`。

| 按键 | 行为 |
| --- | --- |
| ↑ / ↓ | 按最近优先浏览匹配，分别向更旧 / 更新切换 |
| → / End | 接受整条预览，不执行 |
| 打字、粘贴或其他编辑键 | 移除预览尾部，再执行原有编辑动作 |
| Enter | 默认只执行已补全的前缀；要执行整条，先按 → 或 End |
| Esc / Ctrl-G | 取消预览，恢复原输入和光标 |
| Alt-D / Shift-Delete | 删除当前预览对应的完整历史记录并保存 |

匹配只使用光标前的文本。连续 ↑/↓ 沿用同一查询；编辑或移动光标后，下一次请求重新匹配。没有匹配时保留原输入。

普通模式只替换 `default` 和 `insert` 模式的 ↑/↓ 历史搜索分支：多行编辑在首行按 ↑、末行按 ↓ 时搜索，其他行正常移动；Fish 列表导航保持原样。Tab、Shift-Tab、Ctrl-R 等按键保留原绑定，预览中先移除尾部再执行。

删除按大小写敏感的全文精确匹配，相同记录一起删除，取消预览不会撤销删除。推荐 Alt-D：部分终端把 Shift-Delete 发成普通 Delete，可用 `fish_key_reader` 检查。

## 边界

- 大小写敏感，首词从命令开头匹配，后续词保持顺序。按空白分词，不解析引号或 Fish 语法；`m` 不匹配 `"mycontainer"`，`sh` 不匹配 `/bin/sh`。
- 预览是缓冲区中的真实文本和选区，读取命令行的插件会看到整条候选。匹配由 ↑/↓ 触发，不随每次输入自动刷新。
