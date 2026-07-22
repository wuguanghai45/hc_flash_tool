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

## 已完成功能
1. 支持通过jlnk烧录设备（不需要安装jlink）
2. 支持通过esptool烧录设备
3. 支持预设设备列表


## 待完成功能
1. 通过canopen烧录固件
2. 支持通过canopen修改ID
