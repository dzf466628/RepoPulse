# RepoPulse · Git 状态台

一个面向 Windows 的中文 Git 状态总览工具，重点查看多个项目在本地、NAS、GitHub 三处的连接和版本状态。主色为电光蓝 `#4EA1FF`。

## 当前版本

v0.2.0

当前版本重点：

- 管理本地 Git、NAS 和 GitHub 多渠道仓库
- 查看项目连接、commit、一致性和未提交修改状态
- 支持提交并同步、定时同步和修改同步
- 支持项目开关、全量同步和 GitHub 失败忽略
- 支持托盘提交并同步，并在后台完成后通知
- 使用后台线程执行网络检查，避免界面卡死

## 启动

```powershell
python -m pip install -r requirements.txt
python main.py
```

GitHub 私有仓库请先确认本机 SSH 或 Git Credential Manager 已配置好，并按需打开代理。

## 设计边界

本工具优先做“状态查看和快捷入口”，不在第一版重复实现完整的冲突编辑器、rebase、cherry-pick 等高级 Git 功能。
