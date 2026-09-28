# 打包故障记录

## QtGui 加载失败（v0.2.0-v0.2.1）

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