# XBot 中央 Web 后台部署说明

## 访问地址

中央控制器更新后，Web 后台内置在原控制器服务里，不需要单独安装 Node、Nginx 前端或 React 项目。

```text
http://服务器IP:8766/admin
```

如果你已经配置域名和反向代理，则访问：

```text
https://你的域名/admin
```

## 管理员账号

推荐在 Ubuntu 的 systemd 服务里设置环境变量：

```ini
Environment=XBOT_ADMIN_USER=admin
Environment=XBOT_ADMIN_PASSWORD=请换成强密码
Environment=XBOT_ADMIN_SESSION_SECRET=请换成一段长随机字符串
```

如果没有设置，程序会临时使用：

```text
用户名：admin
密码：admin123456
```

公网部署不要使用默认密码。

## Ubuntu 最快更新命令

```bash
cd /opt/xbot
chmod +x deployment/server_update.sh
BRANCH=main APP_DIR=/opt/xbot bash deployment/server_update.sh
```

脚本会自动做这些事：

- 记录当前 commit，方便回滚。
- 备份 `automation/data` 和配置文件。
- `git pull origin main` 拉取最新代码。
- 安装服务端依赖。
- 重启 `xbot-controller`。
- 重启或拉起 Discord Bot。
- 检查 `/health`。

## 设置管理员密码

编辑控制器服务：

```bash
systemctl edit xbot-controller
```

填入：

```ini
[Service]
Environment=XBOT_ADMIN_USER=admin
Environment=XBOT_ADMIN_PASSWORD=请换成强密码
Environment=XBOT_ADMIN_SESSION_SECRET=请换成一段长随机字符串
```

保存后执行：

```bash
systemctl daemon-reload
systemctl restart xbot-controller
systemctl status xbot-controller --no-pager
```

验证：

```bash
curl -fsS http://127.0.0.1:8766/health
```

然后浏览器打开：

```text
http://服务器IP:8766/admin
```

## 当前后台包含的功能

- 管理员登录和退出。
- 首页仪表盘：在线电脑、活跃账号、今日任务、失败任务、待执行调度。
- 电脑管理：node、label、在线状态、Grok 开关、同步分组、最后心跳。
- 分组与账号管理：绑定/解绑别名、查看账号、停用/恢复账号、账号时间线。
- 任务中心：查看 queued、leased、completed、failed、cancelled、preempted 等状态，查看日志，取消任务。
- 账号评分计划：查看计划、计划详情、暂停、恢复、删除、查看/保存/恢复中央评分提示词。
- 调度任务：查看 scheduled_tasks，单个或批量暂停、恢复、取消。
- 审计与设置：查看后台操作审计、token 指纹、数据库路径。

## 回滚

如果更新后异常：

```bash
cd /opt/xbot
ls -lt backups/last_commit_before_update_*.txt | head
cat backups/last_commit_before_update_最近时间.txt
git reset --hard 上一步看到的commit
systemctl restart xbot-controller
systemctl restart xbot-discord || true
```

如果需要恢复数据库备份：

```bash
cd /opt/xbot
tar -xzf backups/controller_db_对应时间.tar.gz
systemctl restart xbot-controller
```
