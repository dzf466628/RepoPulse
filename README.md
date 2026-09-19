# RepoPulse · Git 状态台

一个面向 Windows 的中文 Git 状态总览工具，重点查看多个项目在本地、NAS、GitHub 三处的连接和版本状态。主色为电光蓝 `#4EA1FF`。

## 当前版本

v0.2.2

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

## 打包故障记录

### QtGui 加载失败（v0.2.0-v0.2.1）

现象：安装后启动出现 `DLL load failed while importing QtGui`，进一步的系统错误为 Qt6Core 找不到 `ucnv_open` 入口。

根因：PyInstaller 构建环境中误收集了 Poppler 的 `icuuc.dll`。该文件只导出带版本后缀的 `ucnv_open_78`，而 Qt6Core 需要未带后缀的 `ucnv_open`。另外，Inno Setup 默认不会删除新版本中已经移除的旧文件，因此旧安装目录里的 ICU 文件会继续被加载。

修复：

- `RepoPulse.spec` 排除 `icuuc.dll` 和 `icudt78.dll`
- `installer.iss` 的 `[InstallDelete]` 清理旧安装残留
- 使用目录版打包，Qt DLL 与 PyQt6 扩展一起安装
- 使用控制台 bootloader 的 `hide-early` 模式，避免 PyQt6 在无控制台 bootloader 下加载异常

验证：在干净 PATH 下覆盖安装到 `D:\RepoPulse`，确认安装目录没有 ICU 残留，并能正常显示 RepoPulse 主窗口。

以后打包必须检查：

1. `dist\RepoPulse\_internal` 中不能出现外部环境带入的 ICU DLL。
2. 必须从实际安装目录启动 EXE，而不是只验证开发目录。
3. 升级安装时必须显式清理已经移除的运行库文件。
