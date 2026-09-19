# RepoPulse · Git 状态台

一个面向 Windows 的中文 Git 状态总览工具，重点查看多个项目在本地、NAS、GitHub 三处的连接和版本状态。主色为电光蓝 `#4EA1FF`。

## 当前版本

第一版重点：

- 添加、编辑、删除多个本地 Git 项目
- 查看当前分支、HEAD、未提交文件、冲突、ahead / behind
- 检查 NAS 和 GitHub 仓库连接
- 比较本地与远程 commit 是否一致
- 显示完整状态信息和 Git 操作日志
- 使用后台线程执行网络检查，避免界面卡死

## 启动

```powershell
python -m pip install -r requirements.txt
python main.py
```

GitHub 私有仓库请先确认本机 SSH 或 Git Credential Manager 已配置好，并按需打开代理。

## 设计边界

本工具优先做“状态查看和快捷入口”，不在第一版重复实现完整的冲突编辑器、rebase、cherry-pick 等高级 Git 功能。
