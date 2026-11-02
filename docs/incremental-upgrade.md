# 0.1.2 → 0.1.3 轻量增量升级

本包只带应用 wheel、变更代码和升级脚本，不重复打包 Python 依赖、基础镜像、卡池、干员头像缓存或个人数据。
内置默认剪影属于应用资源，随 wheel 一起提供。
服务器必须已经安装 0.1.2，并保留本地镜像 `ark-gacha-simulator:0.1.2`。
升级会使用该镜像离线构建 `ark-gacha-simulator:0.1.3`，无需访问镜像仓库或下载依赖。
支持与旧镜像相同的架构；本次在 Linux amd64 验证。

## 上传

在开发机项目目录的 PowerShell 中执行，替换服务器地址：

```powershell
scp `
  .\dist\ark-gacha-upgrade-0.1.2-to-0.1.3-linux.tar.gz `
  .\dist\ark-gacha-upgrade-0.1.2-to-0.1.3-linux.tar.gz.sha256 `
  root@服务器公网IP:/root/ark-gacha-simulator/
```

## 执行升级

以下在服务器执行；部署目录不同需修改最后一个参数。使用有权访问 Docker 和现有配置、数据目录的账号运行。

```bash
(
set -eu
cd /root/ark-gacha-simulator
sha256sum -c ark-gacha-upgrade-0.1.2-to-0.1.3-linux.tar.gz.sha256
tar -xzf ark-gacha-upgrade-0.1.2-to-0.1.3-linux.tar.gz
bash ark-gacha-upgrade-0.1.2-to-0.1.3/upgrade.sh \
  /root/ark-gacha-simulator/ark-gacha-simulator
)
```

脚本先核对包内每个文件的哈希、部署版本与本地旧镜像，完成新镜像构建后再停机器人。
旧代码备份为 `ark-gacha-backups/before-0.1.3-时间-随机后缀/code.tar.gz`，
停机后的 `.env` 与 `data/` 备份为同目录的 `state.tar.gz`，终端会显示实际备份路径。
随后覆盖变更文件并只重建 `bot` 服务，用 `--no-build --pull never` 避免拉取远端镜像。
自动检查安装版本和监听端口；切换失败时恢复旧代码并尝试启动旧镜像。

现有 `.env`、数据库、额度、水位、选池和 NapCat 登录资料继续使用；无需重新配置或扫码。
十分钟十连历史为内存数据，会随重启清空。

## 验收

```bash
cd /root/ark-gacha-simulator/ark-gacha-simulator
docker compose exec -T bot python -c "from importlib.metadata import version; print(version('ark-gacha-simulator'))"
docker compose ps
docker compose logs --tail=60 bot
```

版本应为 `0.1.3`。头像缺失、损坏或仍在下载时，十连和一井应先使用默认剪影出图，
姓名、星级与抽数继续显示，后台下载完成后后续出图自动使用真实头像。
剪影图成功提交后正常计次；历史查询保留当时原图。切池预下载与 `validate --assets` 继续严格校验头像。
同时检查四个指令的 @回复和一井的实际抽数标记。
端口检查不代表 QQ 已登录或消息已经投递，群内验收需要在目标主机完成。

## 手动回滚

将 `backup` 改成脚本输出的实际备份目录。数据库结构未改变，代码回滚默认继续使用当前数据：

```bash
(
set -eu
target=/root/ark-gacha-simulator/ark-gacha-simulator
backup=/root/ark-gacha-simulator/ark-gacha-backups/before-0.1.3-实际目录
cd "$target"
docker compose stop bot
tar -xzf "$backup/code.tar.gz" -C "$target"
docker compose up -d --no-build --pull never --force-recreate bot
)
```

如需恢复业务数据，再停机恢复同一份 `state.tar.gz`；此操作会回退备份后的抽卡进度。

## 开发机重新打包

本节对应历史 0.1.3 源码。当前 0.1.4 源码已新增 `regex`，旧构建器会拒绝版本或依赖变更，
不能用当前源码重新生成同名历史包。开发机 `dist/` 已有的 0.1.2 → 0.1.3 包保持原样。
当前版本打包命令见 [0.1.4 最小更新手顺](minimal-upgrade.md#开发机重新打包)。

构建器按已核验的 0.1.2 发布包元数据检查项目依赖和锁定清单一致，并检查 wheel 中的变更代码与当前源码一致。
发布包保存在本地 `dist/`，校验文件使用 Linux LF 换行。
