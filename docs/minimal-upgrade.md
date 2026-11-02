# 0.1.3 → 0.1.4 最小离线更新

适用于已有 **0.1.3、Linux amd64（x86_64）、Docker Compose** 的部署，服务器需保留
`ark-gacha-simulator:0.1.3` 本地镜像。其他架构和原生 systemd 不适用此包。

更新包只包含应用 wheel、新增的 `regex==2026.9.29` CPython 3.11 Linux amd64 wheel、
应用源码、升级脚本与文档。不包含完整镜像、既有依赖、卡池、干员头像缓存、`.env`、数据库或个人规则。
内置默认剪影和默认关闭的规则示例是应用资源。

升级全程离线：复用旧镜像，`docker build --pull=false --network=none`，
以 `uv pip install --offline --no-deps` 安装包内两个 wheel，并运行依赖一致性检查。
开发机打包时会从锁定 URL 下载新增依赖，并核对 `uv.lock` 中的 SHA-256。

## 上传与升级

上传开发机 `dist/` 中以下两个文件到服务器同一目录：

- `ark-gacha-upgrade-0.1.3-to-0.1.4-linux-amd64.tar.gz`
- `ark-gacha-upgrade-0.1.3-to-0.1.4-linux-amd64.tar.gz.sha256`

在服务器执行，最后一个参数改为现有部署目录。使用有权访问 Docker、配置与数据的账号：

```bash
(
set -eu
sha256sum -c ark-gacha-upgrade-0.1.3-to-0.1.4-linux-amd64.tar.gz.sha256
tar -xzf ark-gacha-upgrade-0.1.3-to-0.1.4-linux-amd64.tar.gz
bash ark-gacha-upgrade-0.1.3-to-0.1.4/upgrade.sh \
  /root/ark-gacha-simulator/ark-gacha-simulator
)
```

脚本先检查包内文件、部署版本、旧镜像架构、Python 和现有依赖版本，再离线构建新镜像。
构建成功后备份旧代码、停机器人、备份 `.env` 与整个 `data/`，随后替换应用文件并只重建 `bot`。
备份目录在部署目录同级的 `ark-gacha-backups/before-0.1.4-时间-随机后缀/`，终端会显示完整路径。
切换后检查安装版本和监听端口，失败时恢复旧代码、移除新引入的文件并尝试重启旧镜像。

现有 `.env`、自动回复规则、数据库、额度、水位、选池、卡池与头像缓存继续使用。
NapCat 独立运行，登录资料不受此包影响。
重启会清空内存中的十分钟十连历史、自动回复冷却和消息去重状态。

## 验收与自动回复

```bash
cd /root/ark-gacha-simulator/ark-gacha-simulator
docker compose exec -T bot python -c "from importlib.metadata import version; print(version('ark-gacha-simulator')); print(version('regex'))"
docker compose ps
docker compose logs --tail=60 bot
```

版本应分别为 `0.1.4` 与 `2026.9.29`。群内检查十连、一井、历史、@回复，以及管理员卡池操作。
通过规则校验的未来卡池也允许选择，`latest` 选择数据中开放时间最新的可用池。
端口就绪只代表应用启动，真实 QQ 登录和消息投递需在目标环境验收。

已有 `data/auto-replies.toml` 原样保留。若从未配置，可执行：

```bash
docker compose exec -T bot ark-gacha replies init
docker compose exec -T bot ark-gacha replies check
```

`init` 不会覆盖已有文件；示例默认关闭，只有 `#sl` 全文匹配和 @机器人且含“是谁”两条规则，
回复均为空。填写后开启，规则通常在 5 秒内自动加载。
完整字段、多行文字与正则说明见 [自动回复文档](auto-replies.md)。

## 手动回滚

将 `target` 与 `backup` 改成实际目录：

```bash
(
set -eu
target=/root/ark-gacha-simulator/ark-gacha-simulator
backup=/root/ark-gacha-simulator/ark-gacha-backups/before-0.1.4-实际目录
cd "$target"
docker compose stop bot
python3 -B "$backup/restore-code.py" "$target" "$backup"
docker compose up -d --no-build --pull never --force-recreate bot
)
```

数据库结构未变化，回滚默认保留当前数据。
需要恢复业务数据时，先停机，再恢复该备份中的 `state.tar.gz`；这会回退备份之后的抽卡进度与规则修改。

## 开发机重新打包

```powershell
uv build --wheel --offline --out-dir output/release-0.1.4/wheels
uv run --locked python scripts/build_minimal_package.py --wheel output/release-0.1.4/wheels/ark_gacha_simulator-0.1.4-py3-none-any.whl
```

可通过 `--dependency-wheel <路径>` 使用已下载的锁定 wheel。
构建器只允许相对 0.1.3 基线新增指定 `regex` 依赖，拒绝其他依赖漂移，并核对 wheel 与全部应用源码一致。
旧版 0.1.2 → 0.1.3 打包器继续保留其原始限制。
