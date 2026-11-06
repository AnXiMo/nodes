# Nodes

默认使用 Instant Mail 分配 `gmail.com` 邮箱，完成 ProxyScrape 注册、邮箱验证码接收与 HTTP 代理导出，支持 GitHub Actions、Docker 和 Linux，也兼容 Cloud Mail。

Gmail 协议移植自 AnXiMo 的 `Instant_mail/instant_mail_api.py`，无需启动原项目、浏览器扩展或提供 Gmail 密码。验证码由 ProxyScrape 发送，程序通过 Instant Mail 的列表和邮件详情接口收取，仅使用本次操作后出现且收发地址匹配的验证码。邮箱凭据自动保存在 `account/instant-mail/`。

[![CI](https://github.com/dnslin/nodes/actions/workflows/ci.yml/badge.svg)](https://github.com/dnslin/nodes/actions/workflows/ci.yml)
[![Daily](https://github.com/dnslin/nodes/actions/workflows/daily.yml/badge.svg)](https://github.com/dnslin/nodes/actions/workflows/daily.yml)
[![GHCR](https://img.shields.io/badge/GHCR-amd64%20%7C%20arm64-2496ED?logo=docker&logoColor=white)](https://github.com/dnslin/nodes/pkgs/container/nodes)

- **手动运行**：GitHub Actions 直接运行 Python，无需为 Nodes 单独部署服务器；定时触发当前关闭。
- **Resin 同步**：所有账号的节点合并到一个专用订阅，保留已有节点并去重。
- **服务器部署**：Docker 内置浏览器和虚拟显示器，账号与节点自动取回本机。

## GitHub Actions

1. Fork 本仓库，按[配置指南](docs/actions.md#配置)添加 Resin 的 2 项 Secrets；默认 Gmail 无需邮箱 Secrets。
2. 在 **Actions → Daily Nodes** 中启用工作流，先运行默认的环境检查，再取消“仅检查运行环境”执行实际任务。
3. 手动填写数量执行，导出后自动同步 Resin。当前未配置定时触发。

本方式不保存账号或节点附件，Resin 需要保持在线。定时触发可能延迟；完整运行规则见[每日运行指南](docs/actions.md)。

## Docker / 本机运行

需要 Docker、Docker Compose 和 POSIX shell；Windows 推荐 WSL。默认 Gmail 无需邮箱账号配置。

```bash
git clone https://github.com/AnXiMo/nodes.git
cd nodes
cp .env.example .env
# 保持 MAIL_PROVIDER=instant_mail，可选配置 Resin。
docker compose build
./scripts/run-docker.sh --check-mail
./scripts/run-docker.sh --count 1 --threads 1 --attempts 1 --hide-browser
```

账号保存在 `account/`，代理保存在 `node/`，无需进入容器。`--check-mail` 会创建一个 Gmail 邮箱并检查收信接口。已有账号可用 `./scripts/run-docker.sh --resume EMAIL` 继续处理，包括带有 `mailbox_id` 的 Gmail 未完成验证账号。Linux 直接安装、配置字段和故障处理见[使用指南](docs/usage.md)。

试用资格、代理数量和验证服务是否接受请求由服务端决定；导出成功不代表每个代理都已通过连通性检查。

## 文档

- [每日运行指南](docs/actions.md)：Secrets、定时任务与手动运行。
- [使用指南](docs/usage.md)：配置、本机平台、命令参数与 Resin 同步。
- [开发指南](docs/development.md)：目录结构、测试与镜像发布。
- [更新记录](CHANGELOG.md) · [问题反馈](https://github.com/dnslin/nodes/issues)

## 来源与授权

基于 [kingenbomb/Nodes](https://github.com/kingenbomb/Nodes/tree/1216835354d0c68b768b59ce137b75f40fe3e9ac) 改造，作为独立仓库维护。上游未声明许可证，本仓库暂未声明统一开源许可证，保留原作者权利。使用时请遵守相关服务条款，仅操作有权使用的账号。
