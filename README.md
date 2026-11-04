# MaiBot SnowLuma Adapter（统一 QQ 连接器）

## Neko 私聊回传维护修订（2026-11-04）

基于官方 v1.1.13 / `9b409b1920291f5f12f31c68b0b41604ea40a78a`，保留原作者、许可证、插件ID和版本，
以维护提交区分源码，不冒充官方新版本。自身私聊回传保留真实发送者并将 `target_id` 透传为会话对端；
缺少有效接收者、缺群号的群消息拒绝解析，避免创建错误私聊。显式接收 `message_sent`，
仍遵守忽略自身消息、发送者和对端黑名单；不更改配置或登录状态。

**必须配套修复宿主的私聊会话/名单/身份处理**（`znzsofficial/MaiBot` 维护线的私聊自身回传补修）。
仅透传接收者无法修正旧宿主仍按发送者创建会话的问题。旧历史不自动迁移或删除。
测试使用合成消息，无QQ发送或模型调用。用户已授权配套发布，运行版本以实例验收记录为准。
维护分支 `neko/main`，上游镜像保留；同提交Linux生产/Windows最低SDK2.20测试、隔离源码包及
ci-gate/publish-gate全部通过后才允许标准热更。运行 `python scripts/check.py` 和
`python scripts/package.py`，不自动发布官方包或部署。保原1.1.13版本，以Git SHA和文件摘要区分修订。

需要 MaiBot >= 1.3.18。

连接 SnowLuma / NapCat 的 MaiBot 适配器插件。

1.0 版起，本插件同时支持两类客户端：连接建立后自动判断连接方为 NapCat 还是 Snowluma

## 使用方法

1.在 Maibot webui中插件市场下载或者github下载压缩包并解压到插件plugins目录

2.在napcat/snowluma中创建正向ws(ws服务器)连接，记录端口和token连接令牌

3.在适配器配置中正确填写对应的端口的token令牌

4.在 Maibot Webui中的适配器设置中的 "黑白名单规则" 配置群权限，默认全部阅读（就是所有群消息都接受），可以自行改为全部不阅读（只添加阅读的群号）

## 群消息发送确认

连接 SnowLuma 时，群消息及合并转发在动作返回后还会查询对应消息，
只有消息 ID、目标群和正数群消息序号均匹配，才向 MaiBot 报告发送成功。
本地合成记录（序号为 0）、仅受理的异步动作或查询失败会报告失败；
“投递未确认”表示尚无真实群消息回执，消息仍可能迟到，适配器不会自动重发。

## 文本转 QQ 表情

在 WebUI 的适配器「插件设置」中开启「启用文本转QQ表情」（默认关闭），
或将 `[plugin]` 下的 `enable_text_face_conversion` 设为 `true`。
发送时，`[流泪]` 和 `/流泪` 均会转换为对应的 QQ 原生表情，支持连续或混合使用（如 `[流泪]/捂脸`）；
空格、标点、其他消息段和未知名称保持原样。名称必须完整匹配，
例如 `/捂脸不存在` 和 `[流泪不存在]` 不会被部分转换，只转换映射表中存在的表情名。
群聊、私聊及合并转发节点均生效，修改开关后对后续发送生效。

## 文本转 @

在「插件设置」开启「启用文本转@」（默认关闭），对应
`[plugin].enable_text_at_conversion = true`。群聊发送文本中的 `@QQ号`、
`@群名片`、`@昵称` 必须完整、唯一匹配当前群成员才转换为实际 @；
未知名称、重名、邮箱及查询失败时的文本保持原样，不自动转换 @全体成员。
真实 @ 的相邻去重始终生效，不受文本转换开关控制。
相邻的同目标 @（包括中间仅有空格或制表符）合并为一个，兼容宿主已有的实际 @；
不同目标、隔着正文或换行的 @ 保留。私聊与历史合并转发内容不处理。
修改开关后对后续发送生效。

## 其他插件调用 API

通过 SDK 的 `ctx.api.call` 调用，两种前缀均使用 `version="1"`：

```python
member = await self.ctx.api.call(
    "adapter.snowluma.group.get_group_member_info",
    version="1",
    group_id=123456,
    user_id=654321,
)
# 将上面的 adapter.snowluma. 替换为 adapter.napcat.，调用行为完全相同。

data = await self.ctx.api.call(
    "adapter.snowluma.action.call_data",
    version="1",
    action_name="get_group_info",
    params={"group_id": 123456},
)
```

`await self.ctx.api.list()` 可查看已注册的两组名称。前缀不会切换客户端类型；
实际能力仍取决于当前连接的客户端，两个前缀下的 QZone API 均仅支持 SnowLuma。

## 能力差异参考

| 维度 | napcat 画像 | snowluma 画像 |
| --- | --- | --- |
| 发送动作 | `send_group_msg` / `send_private_msg` | 通用 `send_msg` + `message_type` |
| 出站文件 | 内联 `file` 段 | 拆独立 `upload_group_file` / `upload_private_file` |
| 合并转发参数键 | `message` | `messages` |
| 语音 `get_record` 格式 | `wav` | `mp3`（silk 自动转码兜底） |
| QZone API | 不支持（显式报错） | 支持 |

token 鉴权不区分画像：连接时同时附带 `Authorization: Bearer` 头与 URL
`access_token` 参数（OneBot v11 两种标准方式），两类服务端各取所需。

```


## 配置页运行时控件

需要支持运行时控件的新版主程序及 SDK >= 2.19.0。

配置页新增 QQ 连接器状态及“重新连接”按钮。登录仍由 SnowLuma / NapCat 客户端完成：OneBot 接口没有统一扫码登录协议，因此此处不显示二维码。

本地开发可设置 `MAIBOT_PLUGIN_SDK_PATH` 指向新版 SDK 仓库，然后重启主程序。
