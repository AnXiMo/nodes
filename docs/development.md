# 开发与验证

[返回首页](../README.md)

## 安装开发依赖

需要 Python 3.11+：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

## 本地检查

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
.venv/bin/python -m compileall -q src tests tools
sh -n scripts/run-docker.sh
.venv/bin/python -m build
.venv/bin/nodes --help
```

自动化测试使用模拟响应或本地 HTTP 服务，不会创建真实邮箱、注册 ProxyScrape 账号、领取真实试用或写入生产 Resin。覆盖配置优先级、邮箱续登与收信、注册与导出流程、已保存账号恢复、试用领取状态、分页、Resin 订阅合并及启动脚本文件取回。

## 容器检查

```bash
docker compose pull
docker create --name nodes-browser-smoke --shm-size 1g ghcr.io/dnslin/nodes:0.4.0 python /app/smoke.py
docker cp tests/smoke/browser.py nodes-browser-smoke:/app/smoke.py
docker start -a nodes-browser-smoke
docker rm nodes-browser-smoke
.venv/bin/python tests/smoke/container.py
```

生产镜像只安装应用包，不包含测试目录。上面的命令将单个浏览器检查文件复制到临时容器，在本地测试页面上验证 Chromium、虚拟显示器和扩展。检查封闭 Shadow DOM 中的 iframe 时，改为复制 `tests/smoke/turnstile_dom.py`。`container.py` 在 Docker 主机运行，检查退出码和 `docker stop` 的中断处理。这些检查均不请求 ProxyScrape 注册接口。

镜像声明 `STOPSIGNAL SIGINT`，与本地 Ctrl+C 的中断方式一致。容器检查会确认默认停止信号送达应用，应用完成清理后再退出。

`tests/smoke/container.py` 默认检查 `ghcr.io/dnslin/nodes:latest`。使用固定版本或本地构建时，传入实际镜像名，例如 `.venv/bin/python tests/smoke/container.py nodes:local`。

检查真实 Docker 输出取回可执行以下命令。它会复制 Docker 端已有的账号和节点文件，但不创建新账号：

```bash
./scripts/run-docker.sh --help
```

## 模块职责

```text
src/nodes/       应用包及浏览器扩展资源
scripts/        唯一的 Docker 运行及文件取回脚本
tests/          unit 单元测试、integration 本地集成、smoke 环境检查
tools/          可选代理检查工具
docs/           使用与开发指南
pyproject.toml  依赖、命令入口和构建配置
```

| 文件 | 职责 |
| --- | --- |
| `src/nodes/cli.py` | 命令行解析、交互菜单和本地虚拟显示器启动 |
| `src/nodes/workflow.py` | 注册、验证、批量导出与已有账号恢复 |
| `src/nodes/api.py` | ProxyScrape 账号、试用和代理 HTTP 接口 |
| `src/nodes/mail.py` | Cloud Mail 登录、附属邮箱、邮件轮询和验证码识别 |
| `src/nodes/instant_mail.py` | Instant Mail Gmail 协议、邮箱凭据持久化、邮件列表与详情适配 |
| `src/nodes/providers.py` | 邮箱服务选择及旧 Cloud Mail 配置兼容 |
| `src/nodes/browser.py` | Chrome / Chromium 配置和验证组件状态处理 |
| `src/nodes/config.py` | 工作目录中的环境变量、`.env` 和 JSON 配置合并 |
| `src/nodes/storage.py` | 账号快照、代理文件和输出路径 |
| `src/nodes/resin.py` | 将所有导出合并到 Resin 专用本地订阅，刷新及错误报告 |
| `src/nodes/console.py` | 线程标记和控制台日志 |
| `scripts/run-docker.sh` | 容器运行及输出复制到本机 |

`tools/check_proxies.py` 是独立的代理检查工具，不参与默认流程。使用前安装 `.[tools]` 可选依赖，详见[使用指南](usage.md#可选的代理检查)。

扩展资源随 `src/nodes/resources/turnstile/` 打包。安装后通过 `nodes` 或 `python -m nodes` 使用应用，不再依赖根目录中的 Python 文件或修改 `sys.path`。依赖只在 `pyproject.toml` 中维护。

## 验证范围

CI 验证离线流程、安装包和双架构镜像启动；本地浏览器检查验证 Chromium、扩展及虚拟显示器。这些检查不代表真实注册或代理连通性验收。Windows / macOS 完整注册流程和 amd64 真实业务流程仍需在目标环境检查。

## GitHub Actions 与 GHCR

质量检查与镜像发布位于 [`.github/workflows/ci.yml`](../.github/workflows/ci.yml)。

- `main` 推送、PR、版本标签和手动运行：在 Python 3.11 / 3.12 上执行离线测试；执行 Ruff、编译、shell 语法及安装包构建检查。
- 镜像检查：分别使用 `ubuntu-24.04` 和 `ubuntu-24.04-arm` 原生构建 amd64、arm64 镜像，并运行容器命令行帮助检查。CI 不打开真实浏览器或请求注册服务。
- 版本发布：推送 `vX.Y.Z` 标签后，两个架构都通过检查才合并并发布 GHCR 标签。PR 和手动检查不会发布镜像。

GHCR 登录使用仓库自带的 `GITHUB_TOKEN`，不需要创建额外的发布密码。只有发布任务需要 `packages: write` 权限。首次发布的 GHCR 包默认是私有，仓库维护者需要在包设置中将其设为 Public；完成后用户无需登录即可拉取。

发布新版本时先修改 `pyproject.toml` 和 `CHANGELOG.md`，提交并推送到 `main`，再创建对应版本标签，例如：

```bash
git tag -a vX.Y.Z -m 'Release vX.Y.Z'
git push origin vX.Y.Z
```

查看 [Actions](https://github.com/dnslin/nodes/actions/workflows/ci.yml) 结果，确认 GHCR 已发布两个架构后，再发布对应的 GitHub Release。

实现参考：[Docker 多架构构建](https://docs.docker.com/build/ci/github-actions/multi-platform/)、[GitHub Container registry](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)。

每日业务任务独立放在 [`.github/workflows/daily.yml`](../.github/workflows/daily.yml)，直接安装 Python 包并使用运行器自带浏览器，不构建或运行 Docker。默认的手动环境检查仅访问 localhost 页面；定时任务和关闭“仅检查运行环境”的手动运行会使用 Secrets 创建真实账号、导出并同步 Resin，详见[每日运行指南](actions.md)。每日工作流不上传业务文件。

## 提交问题与改动

通过 [Issues](https://github.com/dnslin/nodes/issues) 描述问题，附系统架构、运行方式、版本、复现步骤和相关日志。删除真实邮箱、密码、访问凭据和带认证信息的代理 URL。

修改前阅读相关实现和测试。行为变更应补充可观察结果的测试，并运行相关检查。涉及真实邮箱、注册或试用的验证，应明确操作对象和产生的外部影响。
