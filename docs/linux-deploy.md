# Linux 部署手顺

适用 Linux x86_64 / ARM64。主要入口为 Docker Compose；也提供 Python 3.11 原生运行和 systemd 模板。
部署包包含代码、锁定依赖清单、127 个专属名称卡池及 315 张静态头像；不包含个人 `.env`、SQLite、QQ 登录资料或抽卡结果。
首次构建或安装需要联网获取基础镜像、Python 依赖和中文字体；卡池与头像种子已经随包提供。
另附的 `ark-gacha-simulator-0.1.1-linux-amd64-image.tar.gz` 是已经构建好的 x86_64 镜像；使用该镜像可以跳过构建及依赖下载。

## Docker Compose 部署

需要安装 Docker Engine、Compose v2 和用于初始化的 Python 3。
以下命令在解压后的目录执行，初始化不会覆盖已有 `.env` 或已安装的卡池版本。

```bash
tar -xzf ark-gacha-simulator-0.1.1-linux.tar.gz
cd ark-gacha-simulator
python3 scripts/linux/configure.py
chmod 600 .env
nano .env
```

填写 `ARK_ADMIN_QQ`（实际操作者 QQ）和 `ARK_ALLOWED_GROUPS`（允许的群号），使用 JSON 字符串数组：

```dotenv
ARK_ADMIN_QQ=["123456789"]
ARK_ALLOWED_GROUPS=["987654321"]
```

随机 `ARK_ONEBOT_TOKEN` 已生成，请保留并用于 NapCat。`DEPLOY_UID`、`DEPLOY_GID` 自动使用初始化操作者的 UID/GID，
用于匹配持久化目录权限。不要从 Windows 复制 `.venv` 或包含 Windows 字体路径的配置。

```bash
docker compose build
docker compose run --rm bot validate --assets
docker compose run --rm bot list --limit 10
docker compose run --rm bot select SINGLE_78_0_1
docker compose up -d
docker compose ps
docker compose logs --tail=100 -f bot
```

池 ID 示例取自包内数据；可用 `list` 的实际输出选择其他池。
镜像已安装 Noto Sans CJK 中文字体，Compose 将数据目录固定挂载为 `./data:/app/data`。
镜像启动直接使用已安装的入口，不在每次启动时重新下载依赖。
健康检查验证应用监听端口，不表示 QQ 已登录或消息已成功投递。

如果使用随附的 **Linux amd64 预构建镜像**，将镜像归档放在部署目录的上一级，然后将上面的 `docker compose build` 替换为：

```bash
gzip -dc ../ark-gacha-simulator-0.1.1-linux-amd64-image.tar.gz | docker load
```

之后仍按顺序执行 `validate`、`list` 和 `select`，启动时使用 `docker compose up -d --no-build`。
ARM64 主机使用源码包的 `docker compose build`；不要加载 amd64 镜像。

## NapCat 连接方式

默认要求 **NapCat 和应用位于同一台 Linux 主机**。Compose 使用 `network_mode: host`，应用只监听主机回环地址。
NapCat 在主机上原生运行，或者使用 host 网络的容器运行，然后配置反向 WebSocket 客户端：

| 配置 | 值 |
|---|---|
| URL | `ws://127.0.0.1:8080/onebot/v11/ws` |
| Token | `.env` 的 `ARK_ONEBOT_TOKEN` |
| 消息格式 | Array / 消息段数组 |

NapCat 的普通 bridge 网络容器中的 `127.0.0.1` 指向它自己，不能用此地址访问应用；请按本方案使用 host 网络。
本包提供应用端，不代替 NapCat 安装和 QQ 扫码登录。QQ 登录状态保存在 NapCat 自己的数据目录。
连接保存后，在允许群内验证 `卡池列表`、`方舟卡池`、`方舟十连` 和 `方舟一井`。

默认时区规则仍是北京时间 04:00 刷新，和 Linux 主机采用的显示时区无关。

## 日常操作

```bash
# 查看状态和日志
docker compose ps
docker compose logs --tail=100 bot

# 更新数据，保持当前选池版本
docker compose exec bot ark-gacha sync

# 再次选同一个 ID，才应用新的卡池版本
docker compose exec bot ark-gacha select SINGLE_78_0_1

# 生成离线预览，保存到挂载的数据目录
docker compose exec bot ark-gacha demo SINGLE_78_0_1 --output /app/data/previews

# 修改 .env 后重新创建容器以加载配置
docker compose up -d --force-recreate

# 停止应用
docker compose down
```

更新代码包时先停止应用，保留本机 `.env` 和 `data/`，替换代码后重新构建。
初始化只补充静态头像及首次种子，不会用包内旧版本覆盖已安装的新卡池。

备份和回滚：

```bash
docker compose stop bot
tar -czf "backup-$(date +%Y%m%d-%H%M%S).tar.gz" .env data
docker compose start bot
```

回滚时使用对应旧部署包重新构建；需要回滚业务数据时，停机恢复同一份备份中的 `.env` 和整个 `data/`。
十分钟十连历史是内存状态，重启后自然失效。

## 升级到 0.1.1

适用于已经使用 0.1.0、部署目录为 `/root/ark-gacha-simulator/ark-gacha-simulator` 的 Linux amd64 实例。
在服务器上执行；其他安装路径需要相应替换。0.1.1 无需迁移数据库，已有 `.env`、`data/` 和 `napcat/` 保留。

先在部署目录上一级下载新版本的两个压缩包和校验文件：

```bash
(
set -eu
cd /root/ark-gacha-simulator

for asset in \
  ark-gacha-simulator-0.1.1-linux.tar.gz \
  ark-gacha-simulator-0.1.1-linux.tar.gz.sha256 \
  ark-gacha-simulator-0.1.1-linux-amd64-image.tar.gz \
  ark-gacha-simulator-0.1.1-linux-amd64-image.tar.gz.sha256
do
  curl --fail --location --retry 3 \
    --output "$asset" \
    "https://github.com/IgnorantMelon/ark-gacha-simulator/releases/download/v0.1.1/$asset"
done

sha256sum -c ark-gacha-simulator-0.1.1-linux.tar.gz.sha256
sha256sum -c ark-gacha-simulator-0.1.1-linux-amd64-image.tar.gz.sha256
)
```

两个校验均显示 `OK` 后，先加载镜像，再停应用、备份并覆盖部署包：

```bash
(
set -eu
cd /root/ark-gacha-simulator
docker load --input ark-gacha-simulator-0.1.1-linux-amd64-image.tar.gz

cd /root/ark-gacha-simulator/ark-gacha-simulator
docker compose stop bot

install -d -m 700 /root/ark-gacha-backups
tar -czf "/root/ark-gacha-backups/state-before-0.1.1-$(date +%Y%m%d-%H%M%S).tar.gz" \
  .env data compose.yaml

tar -xzf ../ark-gacha-simulator-0.1.1-linux.tar.gz -C ..
docker compose up -d --no-build --force-recreate bot
docker compose ps
docker compose logs --tail=60 bot
)
```

每个命令成功后再执行后续命令。升级包只覆盖代码、部署脚本、文档和公开种子数据，不包含 `.env`、运行数据或 NapCat 登录资料。
此次升级只重建抽卡应用容器，NapCat 可继续运行；应用启动后反向 WebSocket 会重连。十分钟十连历史随重启清空。

在允许群内检查 `帮助`、`方舟卡池`、`卡池列表 1`、`查看水位`；管理员可用 `切换卡池 latest` 或大小写混合的卡池 ID 测试切池。
`latest` 选择当前已下载数据中的最新可选池，需要刷新上游数据时先执行 `更新卡池`。

如需回滚，停止应用，使用保留的 0.1.0 部署包覆盖代码和 Compose，再加载并启动 `ark-gacha-simulator:0.1.0`。
本次未改变存储结构，通常无需恢复状态；需要恢复时，再从停机备份恢复 `.env` 与完整 `data/`。

## 原生 Python 3.11 部署（可选）

以 Debian / Ubuntu 为例，先安装中文字体和 curl：

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl fonts-noto-cjk
curl -LsSf https://astral.sh/uv/0.11.6/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"

bash scripts/linux/setup.sh
nano .env
.venv/bin/ark-gacha validate --assets
.venv/bin/ark-gacha select SINGLE_78_0_1
bash scripts/linux/start.sh
```

uv 使用 Python 3.11 创建本机虚拟环境，不依赖系统默认 Python 的版本。
如缺少字体，可在 `.env` 指定 `ARK_FONT_PATH` 为实际安装的中文字体绝对路径。
不要同时运行原生进程和 Compose 应用。

systemd 模板是 `deploy/linux/ark-gacha.service`，默认安装位置 `/opt/ark-gacha-simulator`，
用户与组为 `arkgacha`。先创建该服务用户，将项目解压到此位置并交给该用户，
再以该用户执行上述 uv 安装和 `setup.sh`。虚拟环境必须在最终安装路径创建，不能创建后移动。
填好 `.env` 并完成选池后：

```bash
sudo cp deploy/linux/ark-gacha.service /etc/systemd/system/ark-gacha.service
sudo systemctl daemon-reload
sudo systemctl enable --now ark-gacha
sudo systemctl status ark-gacha
sudo journalctl -u ark-gacha -n 100 --no-pager
```

如果修改安装位置或数据路径，请同步修改 unit 的 `WorkingDirectory`、`ExecStart` 和 `ReadWritePaths`。

## 构建方式与溯源

```bash
sha256sum -c ark-gacha-simulator-0.1.1-linux.tar.gz.sha256
```

归档内的 `MANIFEST.json` 记录逐文件 SHA-256；`seed/catalog/versions/` 包含卡池覆盖报告及来源哈希。
源码中的 `scripts/build_linux_package.py` 可重新生成部署包。

构建方式依据 [uv Docker 集成文档](https://docs.astral.sh/uv/guides/integration/docker/)，
网络方式依据 [Docker host 网络文档](https://docs.docker.com/engine/network/drivers/host/)。

本次 Linux 构建与测试范围见 `deploy/linux/VERIFIED.md`。
