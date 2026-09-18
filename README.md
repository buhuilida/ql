# 青龙论坛签到脚本

## 目录

```text
ql_buhuilida/
├─ scripts/                 # 放每个论坛的独立脚本
│  └─ forum_template.py     # 复制后改成具体论坛
├─ requirements.txt
└─ .gitignore
```

## 编写一个论坛脚本

1. 复制 `scripts/forum_template.py`，例如改名为 `example_forum.py`。
2. 修改脚本中的 `CHECKIN_URL`、请求方法、请求头、请求体和成功判断。
3. 在浏览器开发者工具的 Network 中手动签到一次，确认真实请求；不要猜接口，也不要把完整 Cookie 写入代码。
4. 为每个论坛使用独立的环境变量，例如 `EXAMPLE_FORUM_COOKIES`，多账号一行一个 Cookie。
5. 在本机用测试账号运行：

```bash
python scripts/example_forum.py
```

## 导入青龙

在青龙面板中：

1. **订阅管理** 添加 GitHub 仓库地址，或直接把脚本上传到青龙的 `scripts` 目录。
2. 安装依赖：`pip3 install -r requirements.txt`（也可在青龙依赖管理中添加 `requests`）。
3. 在 **环境变量** 添加对应的 Cookie/Token 变量，一行一个账号。
4. 在 **定时任务** 添加命令，例如：

```text
15 8 * * * python3 /ql/data/scripts/example_forum.py
```

5. 先点“运行”验证返回内容，再启用定时任务。登录失效时重新获取 Cookie，不要提交到 GitHub。

## 上传到自己的 GitHub 仓库

在本目录执行：

```bash
git init
git add .
git commit -m "添加论坛签到脚本模板"
git branch -M main
git remote add origin https://github.com/<你的用户名>/<你的仓库>.git
git push -u origin main
```

后续更新：

```bash
git add scripts README.md requirements.txt .gitignore
git commit -m "更新论坛签到脚本"
git push
```

如果 Cookie 曾经误提交到 GitHub，先立即在论坛退出其他会话/刷新 Cookie，再从 Git 历史中清理，不能只删除当前文件。

请确认脚本只用于你有权操作的账号，并遵守目标论坛的服务条款和频率限制。

