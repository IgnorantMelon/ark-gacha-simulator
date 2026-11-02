# 明日方舟 QQ 抽卡模拟机器人

支持 Windows 本机与 Linux 部署，非官方 QQ 接入：**NapCat → OneBot 11 反向 WebSocket → NoneBot2**。
支持历史卡池、十连个人水位、跨群日额度、300 抽独立模拟、头像拼图与十分钟十连历史。

Linux 部署包与 Docker Compose / systemd 使用方式见 [Linux 部署手顺](docs/linux-deploy.md)。以下快速启动使用 Windows PowerShell。
版本更新见 [CHANGELOG](CHANGELOG.md)，已部署的 Linux 实例可按 [升级步骤](docs/linux-deploy.md#升级到-014) 保留配置和数据升级。

## 快速启动

需要 Windows、[uv](https://docs.astral.sh/uv/getting-started/installation/) 和
[NapCat](https://napneko.github.io/)。Python 固定为 3.11，由 uv 管理独立环境。

在项目根目录的 PowerShell 中执行：

```powershell
.\scripts\setup.ps1
.\scripts\sync-data.ps1
```

`setup.ps1` 安装锁定依赖，首次运行生成 `.env` 和随机 OneBot Token；已有 `.env` 不会覆盖。
编辑 `.env`，将下列示例改成实际 QQ 号和群号，保留生成的 Token：

```dotenv
ARK_ADMIN_QQ=["123456789"]
ARK_ALLOWED_GROUPS=["987654321"]
ARK_HOST=127.0.0.1
ARK_PORT=8080
ARK_DATA_DIR=data
```

空群列表表示不响应任何群，空管理员列表表示不开放管理命令。普通群主、群管理员不会自动成为机器人管理员。

启动应用：

```powershell
.\scripts\start.ps1
```

在 NapCat 登录机器人 QQ 后，新建 **WebSocket 客户端／反向 WS** 连接：

| 配置 | 值 |
|---|---|
| 地址 | `ws://127.0.0.1:8080/onebot/v11/ws` |
| Token | `.env` 中的 `ARK_ONEBOT_TOKEN` |
| 消息格式 | Array／消息段数组 |
| 启用连接 | 是 |

保存连接及整个配置，按 NapCat 提示重新启动连接。OneBot Token 与 NapCat WebUI 密码是两项独立配置。
项目不会修改当前 QQ 安装或登录资料。安装与登录请按
[NapCat 文档](https://napneko.github.io/NapCatQQ-Desktop/docs/connections/) 操作。

在允许的群内发送 `卡池列表`，管理员再发送 `切换卡池 <pool_id>`。
切池会预下载该池所有头像，首次操作需要等待网络下载；成功后即可发送 `方舟十连`。
也可在启动前用本机 CLI 选池：

```powershell
uv run ark-gacha list --limit 10
uv run ark-gacha select SINGLE_78_0_1
```

示例池 ID 取自实现时的历史数据；以 `list` 的实际输出为准。首次启动没有默认选池。

## 正则自动回复

支持在允许的群中按正则匹配普通消息，命中后发送文件配置的固定文字，可选 @发送者。
匹配规则与回复统一维护在 `ARK_DATA_DIR/auto-replies.toml`（默认 `data/auto-replies.toml`）：

```text
uv sync --locked
uv run --locked ark-gacha replies init
uv run --locked ark-gacha replies check
```

`init` 不覆盖已有文件，默认提供 `#sl` 全文匹配、@机器人且含“是谁”两条规则，`reply` 留空。
空回复规则暂不发送；填写回复后将顶部 `enabled` 改成 `true`。
默认每 5 秒自动加载变更，配置错误时保留上一版有效规则。已有命令优先，每条普通消息最多触发一条回复。
本次新增 `regex` 依赖，首次安装后需重启机器人；后续修改规则无需重启。
文件格式、冷却与去重行为、Docker 校验方式见 [正则自动回复](docs/auto-replies.md)。

## 群命令

| 命令 | 输出或行为 |
|---|---|
| `方舟十连` | 十名干员头像，更新个人十连进度，每天 3 次 |
| `方舟一井` | 独立模拟 300 抽，展示全部抽出的六星并标注各自为第几抽，每天 1 次 |
| `方舟卡池` | 当前选中池的池名、直接 UP 六星名称 |
| `方舟卡池 <关键词>` | 搜索池 ID 或池名，仅返回 ID、池名、直接 UP 六星名称 |
| `卡池列表` | 最近 10 个可选池，每行按池名、直接 UP 六星名称、ID 排列 |
| `卡池列表 <页码>` | 每页 10 个，每行按池名、直接 UP 六星名称、ID 排列 |
| `切换卡池 <pool_id>` | 管理员为所有群统一切池，ID 大小写不敏感 |
| `切换卡池 latest` | 管理员切换到当前数据中开放时间最新的可选池，包含未来池，`latest` 大小写不敏感 |
| `更新卡池` | 管理员抓取、校验并发布数据，不改变当前选池 |
| `查看水位` | 仅显示连续未出六星和本池累计抽数 |
| `查看历史` | 查看本人十分钟内最近一次十连原图 |
| `help` / `/help` / `帮助` / `/帮助` | 仅列出指令，不附解释 |

`方舟十连`、`方舟一井`、`查看水位`、`查看历史` 的每条回复均 @发起命令的用户，包括错误与额度提示。

十连图与三百抽图均不显示卡池 ID、顶部六星统计行及底部提示文字。
保留标题、干员头像与名称和星级；十连图不显示逐卡序号，底部显示剩余次数。
三百抽图在每张六星干员卡片下标注本次模拟中的实际抽数（如“第78抽”），重复干员分别标注，分页后保持原始抽数；底部只显示六星合计，不显示剩余次数和页码。

抽卡出图时优先使用有效的本地头像。头像缺失、损坏或仍在下载时，使用内置透明剪影替代，
姓名、星级和抽数照常显示，并在后台下载真实头像；后续出图自动使用下载完成的头像。
同一张头像的并发下载会合并，下载失败不影响本次出图。替代图正常生成并提交后仍按正常抽卡计次，
历史查询继续返回当时生成的原图。管理员切池预下载与 `validate --assets` 仍等待并严格校验真实头像。

命令和参数之间用空格；`方舟卡池 123` 是关键词搜索，不是第 123 页。
`切换卡池 latest` 按开放时间倒序选择，同时间按池 ID 排序；选定后固定卡池与数据版本，后续更新不会自动切池。
已替换的旧命令不作为别名保留。需要时可以 @机器人，消息中的 @ 不作为命令正文。

## 额度、水位与历史

- QQ 用户跨群共用额度，每天北京时间 **04:00** 刷新；受理时确定本次额度所属的业务日。
- 六星水位：标准池共用，中坚池另行共用，限定与联动按池隔离。
  首十及定向选调等进度按池保存，切回旧池继续；它们不会随每日额度刷新。
- 图片生成并提交状态后即计次。发送失败不会退次数或回滚水位；生成或提交失败不扣次数。
- 最近一次十连原图仅保存在本进程内存中，生成后 **600 秒**过期。
  新十连覆盖旧图，一井不覆盖；查看不重新抽取、不延长时间、不扣次数。
  切池后仍返回原图；重启后历史不可恢复。发送失败的十连也可查询。
- 一井从零开始，不读取或修改个人十连状态，不保存名单、图片或随机种子。
  每日计次和消息去重元数据会保存。只展示 300 抽实际抽出的六星，不计赠送与契约兑换。

## 数据与规则

只收录有专属卡池名称的标准、联合行动、限定、联动及其他原有支持类型。
排除“适合多种场合的强力干员”这类常规轮换池，以及无名称、新手、自选 UP、首次六星未持有保底等池。
同名活动的不同期或复刻按各自池 ID 保留；通过规则校验的未来池也可查询、选择和抽取，校验失败池不能选择。
列表、搜索、选池和抽卡均不受开放时间限制；开放时间仅用于卡池排序及 `latest` 选择。

名称过滤同样适用于旧数据版本的列表、搜索和选池。原来选中的池如果是常规轮换池，
程序会提示管理员重新选择有专属名称的池，并停止在该池抽取，不会因此扣次数。
用户已保存的额度和水位不因收录范围调整而清空。

来源：

- [ArknightsGameData](https://github.com/Kengxxiao/ArknightsGameData)：卡池元数据与干员字典。
- [PRTS 寻访详情](https://weedy.prts.wiki/gacha_table.json)：历史名单、概率、权重及规则文本。
- [ArknightsGameResource](https://github.com/yuanyan3060/ArknightsGameResource)：干员头像。
- [官方定向选调示例](https://ak.hypergryph.com/news/5101.html)、
  [官方限定池说明](https://ak.hypergryph.com/news/4916)：公开机制依据。

数据按内容版本保存来源、哈希和抓取时间。下载与校验成功后原子切换数据指针；失败保留旧版。
卡池表和干员字典优先从 GitHub Raw 下载，连接超时、HTTP 错误、无效 JSON、
内容不完整或与详情不匹配时，整组切换到 `jsd.onmicrosoft.cn` 备用源，避免混用两个入口的数据表。
每次连接最多等待 5 秒、每次读取最多等待 20 秒；PRTS 详情源失败后重试一次。
成功更新的报告记录实际下载地址和 SHA-256；发现数据表缺少旧快照已收录的卡池时拒绝发布。
所有源失败或发布失败时，`sync` 返回失败状态，群内“更新卡池”明确提示继续使用本地旧数据。
已有快照、当前选池、额度和水位继续保留，列表、查询和抽卡仍使用本地版本；
首次同步且没有本地快照时会提示暂无可用数据。

头像优先使用已经校验通过的本地 PNG；缺失或损坏时后台依次尝试 `jsd.onmicrosoft.cn`、GitHub Raw，
若仍失败则再尝试一次 `jsd.onmicrosoft.cn`。连接超时为 4 秒、读取超时为 10 秒；
每个响应最多 4 MiB，并在 PNG 解码和尺寸校验通过后原子写入缓存。
头像未下载成功时继续使用内置剪影出图，`validate --assets` 和选池预加载仍要求头像实际可用。

CDN 入口使用 `https://jsd.onmicrosoft.cn/gh/`，保留相同上游仓库的 `@master` / `@main` 分支路径。
该域名是第三方入口；分支缓存可能延迟更新，实际刷新时间取决于该入口及上游缓存。
卡池数据保留 GitHub Raw 优先，头像优先走此 CDN 入口。
备用源默认启用，无需修改 `.env`，也不会自动清理头像缓存或历史快照。

管理选池绑定当时的版本，更新后需重新切换到同一 ID 才应用新版本。
未知池类型或无法识别的规则会列入报告并禁止启用。

详见 `docs/rules.md`。游戏素材版权归各权利人，项目保留来源信息。

## 维护和验证

```powershell
uv run ark-gacha sync
uv run ark-gacha validate
uv run ark-gacha validate --assets
.\scripts\check.ps1
uv run ark-gacha demo SINGLE_78_0_1 --mode ten --output output/preview
uv run ark-gacha demo LIMITED_76_0_1 --mode well --output output/preview
```

`demo` 是显式离线开发工具，会将预览图写入指定目录；不连接 QQ，不使用用户额度或水位。
`--details` 可输出该次离线模拟名单。机器人运行中的一井结果不会经过此导出入口。

运行文件均在 `ARK_DATA_DIR`：

| 路径 | 内容 |
|---|---|
| `bot.sqlite3` | 全局选池、额度、水位、每池进度、请求去重元数据 |
| `catalog/current` | 最新可查询数据版本指针 |
| `catalog/versions/<version>.json` | 规范化的不可变卡池快照 |
| `catalog/versions/<version>.report.json` | 完整覆盖、规则和排除原因报告 |
| `catalog/versions/<version>.audit.json` | 全池模拟与可选的全量头像校验记录 |
| `catalog/sources/<version>/` | 原始数据及溯源依据 |
| `avatars/` | 公用静态头像缓存 |

备份时先停止机器人，再复制 `.env` 和整个数据目录；恢复时放回原位置再启动。
恢复旧版本选池可以执行 CLI `select` 或在群内切换到已验证的池，个人水位不会因此清空。
需要恢复旧的全局状态时，停机恢复备份中的 SQLite 与对应卡池版本文件。
仅运行一个机器人实例；CLI 选池可与机器人并存，数据库事务会保护计次状态。

字体默认使用 Windows 微软雅黑或黑体；需要替换时设置 `ARK_FONT_PATH` 为中文字体的绝对路径。
日志抑制 NoneBot 会包含原文的 INFO 事件日志，错误只记录异常类型。

真实 QQ 测试需在扫码登录、填写 QQ 和群号后完成。模拟 OneBot 测试能验证应用协议与图片输出，不能证明 QQ 平台已实际投递。
