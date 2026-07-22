<p align="center">
  <img src="logo.svg" width="128" height="128" alt="HC Embedded Flash Tool Logo">
</p>

# Embedded Flash Assistant

A desktop tool for flashing embedded devices with J-Link or esptool.

## Setup Instructions

### For Windows Users:
1. Open PowerShell
2. Run: `.\setup.ps1`
3. Activate the virtual environment: `.\venv\Scripts\Activate.ps1`

### For Linux/Unix Users:
1. Open Terminal
2. Run: `bash setup.sh`
3. Activate the virtual environment: `source venv/bin/activate`

After setup, you can run the main application using: `python main.py`

## 打包项目
```
pyinstaller --clean --noconfirm main.spec
```

打包配置会收集 esptool 的子模块和 stub 数据、应用 Logo，并生成无控制台窗口的应用。

### GitHub Actions 自动打包 Windows 版本

仓库中的 `Windows Package` 工作流会在以下场景使用 GitHub 托管的
`windows-latest` Runner 自动执行测试并打包：

- 推送任意 Git tag；
- 在 Actions 页面手动触发。

工作流成功后，可在该次运行的 `Artifacts` 区域下载
`embedded-flash-assistant-windows-*`，其中包含
`embedded-flash-assistant-windows-x64.zip`。压缩包内已经包含 64 位 J-Link
运行库、ST 设备配置和 esptool 资源，不需要在目标 Windows 电脑安装 Python。
解压后运行 `main\main.exe` 即可启动应用。

也可以在 64 位 Windows PowerShell 中执行以下命令生成同样的 ZIP：

```powershell
.\scripts\package_windows.ps1
```

## 已完成功能
1. 支持通过jlnk烧录设备（不需要安装jlink）
2. 支持通过esptool烧录设备
3. 支持预设设备列表


## 待完成功能
1. 通过canopen烧录固件
2. 支持通过canopen修改ID
