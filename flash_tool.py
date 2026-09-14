from PyQt6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QSizePolicy,
    QStyle,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtCore import QSize, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QFontDatabase, QIcon
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import filecmp
import html
import pylink
from typing import Tuple
import esptool
import sys
import shutil
from pathlib import Path
import os
import tempfile
import threading
from serial.tools import list_ports
from device_configs import DEVICE_CONFIGS


_ESPTOOL_OUTPUT_LOCK = threading.Lock()

APP_NAME = "嵌入式烧录助手"
APP_SUBTITLE = "HC Embedded Flash Tool"
LOGO_RESOURCE_NAME = "logo.svg"
BAUDRATE_OPTIONS = ("115200", "230400", "460800", "921600")


class SignalTextStream:
    """将第三方工具的文本输出安全转发为 Qt 信号。"""

    def __init__(self, signal):
        self._signal = signal
        self._buffer = ""

    def write(self, message):
        """缓存文本并逐行发送，避免工作线程直接访问 GUI。"""
        if not message:
            return 0
        self._buffer += str(message).replace("\r", "\n")
        lines = self._buffer.split("\n")
        self._buffer = lines.pop()
        for line in lines:
            line = line.strip()
            if line:
                self._signal.emit(line)
        return len(message)

    def flush(self):
        """发送尚未以换行符结尾的文本。"""
        line = self._buffer.strip()
        self._buffer = ""
        if line:
            self._signal.emit(line)

    def isatty(self):
        """声明该流不是终端，禁止第三方库输出 ANSI 动画。"""
        return False


class TerminalTextEdit(QTextEdit):
    """显示符合 Onsen UI 浅色列表风格的只读终端日志。"""

    def paintEvent(self, event):
        """使用 Qt 标准绘制流程呈现清晰、无装饰纹理的日志内容。"""
        super().paintEvent(event)


def resource_path(relative_path):
    """返回开发环境或 PyInstaller 环境中的资源绝对路径。"""
    base_path = getattr(sys, "_MEIPASS", os.path.abspath("."))
    return os.path.join(base_path, relative_path)


def load_app_icon():
    """从开发目录或打包资源中加载应用 Logo 图标。"""
    logo_path = Path(resource_path(LOGO_RESOURCE_NAME))
    return QIcon(str(logo_path)) if logo_path.is_file() else QIcon()


def create_console_font():
    """从 Qt 已识别的字体中选择一个真实存在的等宽字体。"""
    available_families = QFontDatabase.families()
    installed_families = set(available_families)
    preferred_families = (
        "Menlo",
        "Cascadia Mono",
        "DejaVu Sans Mono",
        "Liberation Mono",
        "Courier New",
        "Monaco",
    )
    family = next(
        (
            candidate
            for candidate in preferred_families
            if candidate in installed_families
        ),
        None,
    )
    if family is None:
        family = next(
            (
                candidate
                for candidate in available_families
                if QFontDatabase.isFixedPitch(candidate)
            ),
            available_families[0] if available_families else "",
        )
    return QFont(family) if family else QFont()


def load_device_configs():
    """返回设备配置的浅拷贝，避免窗口修改模块全局对象。"""
    return dict(DEVICE_CONFIGS)


class FlashToolWindow(QMainWindow):
    """提供设备选择、固件配置与烧录进度展示的主窗口。"""

    def __init__(self):
        """初始化应用品牌区域、烧录表单与状态控件。"""
        super().__init__()

        # 从JSON文件加载设备配置
        self.device_configs = load_device_configs()
            
        self.setWindowTitle(APP_NAME)
        self.setFont(QFont("Arial", 10))
        self.setWindowIcon(load_app_icon())
        self.resize(980, 820)
        self.setMinimumSize(860, 640)

        # 创建中央小部件
        central_widget = QWidget()
        central_widget.setObjectName("centralWidget")
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(18, 18, 18, 16)
        main_layout.setSpacing(12)

        # 品牌区域在主窗口内持续展示，避免仅依赖不同系统表现不一的标题栏图标。
        brand_frame = QFrame()
        brand_frame.setObjectName("brandFrame")
        brand_layout = QHBoxLayout(brand_frame)
        brand_layout.setContentsMargins(18, 12, 18, 12)
        brand_layout.setSpacing(14)

        self.logo_label = QLabel()
        self.logo_label.setObjectName("logoLabel")
        self.logo_label.setFixedSize(62, 62)
        self.logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.logo_label.setAccessibleName("慧仓嵌入式烧录助手 Logo")
        logo_pixmap = load_app_icon().pixmap(62, 62)
        self.logo_label.setPixmap(logo_pixmap)
        self.logo_label.setToolTip(APP_SUBTITLE)
        brand_layout.addWidget(self.logo_label)

        brand_text_layout = QVBoxLayout()
        brand_text_layout.setContentsMargins(0, 0, 0, 0)
        brand_text_layout.setSpacing(2)

        title_label = QLabel(APP_NAME)
        title_label.setObjectName("brandTitleLabel")
        subtitle_label = QLabel(APP_SUBTITLE)
        subtitle_label.setObjectName("brandSubtitleLabel")
        brand_text_layout.addWidget(title_label)
        brand_text_layout.addWidget(subtitle_label)
        brand_layout.addLayout(brand_text_layout)
        brand_layout.addStretch()

        console_badge_frame = QFrame()
        console_badge_frame.setObjectName("consoleBadgeFrame")
        console_badge_layout = QHBoxLayout(console_badge_frame)
        console_badge_layout.setContentsMargins(11, 5, 11, 5)
        console_badge_layout.setSpacing(7)
        self.ready_indicator = QLabel("●")
        self.ready_indicator.setObjectName("readyIndicator")
        self.ready_indicator.setAlignment(Qt.AlignmentFlag.AlignCenter)
        console_badge = QLabel("DEVICE FLASHING CONSOLE")
        console_badge.setObjectName("consoleBadge")
        console_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        console_badge_layout.addWidget(self.ready_indicator)
        console_badge_layout.addWidget(console_badge)
        brand_layout.addWidget(console_badge_frame)
        brand_layout.setAlignment(self.logo_label, Qt.AlignmentFlag.AlignVCenter)
        brand_layout.setAlignment(console_badge_frame, Qt.AlignmentFlag.AlignVCenter)
        main_layout.addWidget(brand_frame)

        self._pulse_visible = False
        self.ready_pulse_timer = QTimer(self)
        self.ready_pulse_timer.setInterval(700)
        self.ready_pulse_timer.timeout.connect(self.toggle_ready_pulse)
        
        # 创建可随状态文本自动扩展的顶部区域
        self.top_frame = QFrame()
        self.top_frame.setObjectName("panelFrame")
        self.top_frame.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Minimum,
        )
        
        # 顶部Frame的布局
        top_layout = QVBoxLayout(self.top_frame)
        top_layout.setContentsMargins(16, 13, 16, 14)
        top_layout.setSpacing(10)

        status_heading_layout = QHBoxLayout()
        status_heading_layout.setContentsMargins(0, 0, 0, 0)
        status_heading = QLabel("系统状态")
        status_heading.setObjectName("sectionTitle")
        status_index = QLabel("STATUS / 01")
        status_index.setObjectName("sectionIndex")
        status_heading_layout.addWidget(status_heading)
        status_heading_layout.addStretch()
        status_heading_layout.addWidget(status_index)
        top_layout.addLayout(status_heading_layout)
        
        # 状态标签
        self.status_label = QLabel("系统待命 · 请选择目标设备")
        self.status_label.setObjectName("statusLabel")
        self.status_label.setWordWrap(True)
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.status_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.status_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self.status_label.setMinimumHeight(42)
        self.status_label.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Minimum,
        )
        top_layout.addWidget(self.status_label)
        
        # 设备选择区域
        device_frame = QFrame()
        device_frame.setObjectName("transparentFrame")
        device_layout = QHBoxLayout(device_frame)
        device_layout.setContentsMargins(0, 0, 0, 0)
        device_layout.setSpacing(10)
        
        device_label = QLabel("目标设备")
        device_label.setObjectName("fieldLabel")
        device_label.setFixedWidth(72)
        device_label.setFixedHeight(38)
        
        self.device_combo = QComboBox()
        self.device_combo.setObjectName("deviceCombo")
        self.device_combo.setFixedWidth(250)
        self.device_combo.setFixedHeight(38)
        
        # 动态生成设备类型列表
        self.device_combo.addItem("请选择设备")
        for device_name in self.device_configs.keys():
            self.device_combo.addItem(device_name)
        
        self.device_combo.currentTextChanged.connect(self.on_device_changed)
        
        device_layout.addWidget(device_label)
        device_layout.addWidget(self.device_combo)

        self.device_detail_frame = QFrame()
        self.device_detail_frame.setObjectName("deviceDetailFrame")
        device_detail_layout = QHBoxLayout(self.device_detail_frame)
        device_detail_layout.setContentsMargins(12, 5, 12, 5)
        device_detail_layout.setSpacing(18)

        selector_items = (
            ("端口 / 接口", "portCombo", 220, "点击选择端口或接口"),
            ("速率", "baudrateCombo", 130, "点击选择通信速率"),
        )
        selectors = []
        for caption, object_name, minimum_width, accessible_name in selector_items:
            detail_layout = QVBoxLayout()
            detail_layout.setContentsMargins(0, 0, 0, 0)
            detail_layout.setSpacing(1)
            caption_label = QLabel(caption)
            caption_label.setObjectName("detailCaption")
            selector = QComboBox()
            selector.setObjectName(object_name)
            selector.setMinimumWidth(minimum_width)
            selector.setFixedHeight(25)
            selector.setPlaceholderText("--")
            selector.setAccessibleName(accessible_name)
            selector.setToolTip(accessible_name)
            detail_layout.addWidget(caption_label)
            detail_layout.addWidget(selector)
            device_detail_layout.addLayout(detail_layout)
            selectors.append(selector)

        self.port_combo, self.baudrate_combo = selectors
        self.port_combo.view().setMinimumWidth(340)

        state_layout = QVBoxLayout()
        state_layout.setContentsMargins(0, 0, 0, 0)
        state_layout.setSpacing(1)
        state_caption = QLabel("状态")
        state_caption.setObjectName("detailCaption")
        self.detail_state_value = QLabel("等待选择")
        self.detail_state_value.setObjectName("detailStateValue")
        self.detail_state_value.setFixedHeight(25)
        state_layout.addWidget(state_caption)
        state_layout.addWidget(self.detail_state_value)
        device_detail_layout.addLayout(state_layout)

        self.port_combo.currentTextChanged.connect(
            lambda _value: self.update_device_details(
                self.device_combo.currentText()
            )
        )
        self.baudrate_combo.currentTextChanged.connect(
            lambda _value: self.update_device_details(
                self.device_combo.currentText()
            )
        )
        device_layout.addWidget(self.device_detail_frame, 1)
        
        top_layout.addWidget(device_frame)
        
        # 添加顶部Frame到主布局
        main_layout.addWidget(self.top_frame)
        
        # 创建固件选择区域Frame
        firmware_frame = QFrame()
        firmware_frame.setObjectName("panelFrame")
        firmware_frame.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Minimum,
        )
        
        # 固件选择区域布局
        firmware_layout = QVBoxLayout(firmware_frame)
        firmware_layout.setContentsMargins(16, 13, 16, 14)
        firmware_layout.setSpacing(10)
        firmware_heading_layout = QHBoxLayout()
        firmware_heading_layout.setContentsMargins(0, 0, 0, 0)
        firmware_heading = QLabel("固件配置")
        firmware_heading.setObjectName("sectionTitle")
        firmware_index = QLabel("FIRMWARE / 02")
        firmware_index.setObjectName("sectionIndex")
        firmware_heading_layout.addWidget(firmware_heading)
        firmware_heading_layout.addStretch()
        firmware_heading_layout.addWidget(firmware_index)
        firmware_layout.addLayout(firmware_heading_layout)

        firmware_content = QWidget()
        firmware_content.setObjectName("firmwareContent")
        self.firmware_buttons_layout = QVBoxLayout(firmware_content)
        self.firmware_buttons_layout.setContentsMargins(0, 0, 0, 0)
        self.firmware_buttons_layout.setSpacing(9)
        firmware_layout.addWidget(firmware_content)
        
        # 添加固件选择Frame到主布局
        main_layout.addWidget(firmware_frame)
        
        log_heading_layout = QHBoxLayout()
        log_heading_layout.setContentsMargins(2, 1, 2, 0)
        log_heading = QLabel("运行日志")
        log_heading.setObjectName("sectionTitle")
        log_index = QLabel("CONSOLE / 03")
        log_index.setObjectName("sectionIndex")
        log_heading_layout.addWidget(log_heading)
        log_heading_layout.addStretch()
        log_heading_layout.addWidget(log_index)
        main_layout.addLayout(log_heading_layout)

        log_control_frame = QFrame()
        log_control_frame.setObjectName("logControlFrame")
        log_control_layout = QHBoxLayout(log_control_frame)
        log_control_layout.setContentsMargins(8, 6, 8, 6)
        log_control_layout.setSpacing(8)
        self.log_filter_input = QLineEdit()
        self.log_filter_input.setObjectName("logFilterInput")
        self.log_filter_input.setPlaceholderText(
            "⌕  搜索日志，或筛选 INFO / WARNING / ERROR"
        )
        self.log_filter_input.setClearButtonEnabled(True)
        self.log_filter_input.textChanged.connect(self.render_log)
        clear_log_button = QPushButton("清除日志")
        clear_log_button.setObjectName("logClearButton")
        clear_log_button.setFixedSize(88, 32)
        clear_log_button.clicked.connect(self.clear_log)
        log_control_layout.addWidget(self.log_filter_input, 1)
        log_control_layout.addWidget(clear_log_button)
        main_layout.addWidget(log_control_frame)
        
        # 创建输出文本编辑器
        self.log_messages = []
        self.text_edit = TerminalTextEdit(self)
        self.text_edit.setObjectName("consoleOutput")
        self.text_edit.setFont(create_console_font())
        self.text_edit.setReadOnly(True)
        self.text_edit.setPlaceholderText("等待烧录任务输出…")
        main_layout.addWidget(self.text_edit)

        # 创建底部按钮Frame
        bottom_frame = QFrame()
        bottom_frame.setObjectName("bottomFrame")
        bottom_frame.setFixedHeight(52)
        bottom_layout = QHBoxLayout(bottom_frame)
        bottom_layout.setContentsMargins(0, 0, 0, 0)

        warning_icon = QLabel("⚠")
        warning_icon.setObjectName("warningIcon")
        safety_label = QLabel("烧录前请确认目标板供电与连接稳定")
        safety_label.setObjectName("safetyLabel")
        bottom_layout.addWidget(warning_icon)
        bottom_layout.addWidget(safety_label)
        bottom_layout.addStretch()

        self.flash_progress = QProgressBar()
        self.flash_progress.setObjectName("flashProgress")
        self.flash_progress.setRange(0, 0)
        self.flash_progress.setTextVisible(False)
        self.flash_progress.setFixedSize(84, 5)
        self.flash_progress.setVisible(False)
        bottom_layout.addWidget(self.flash_progress)
        bottom_layout.addSpacing(10)
        
        # 添加烧录按钮
        self.flash_button = QPushButton("开始烧录  →")
        self.flash_button.setObjectName("primaryButton")
        self.flash_button.setFixedWidth(156)
        self.flash_button.setFixedHeight(42)
        self.flash_button.clicked.connect(self.handle_flash_button)
        
        bottom_layout.addWidget(self.flash_button)
        
        # 添加底部Frame到主布局
        main_layout.addWidget(bottom_frame)
        
        # 初始化固件路径和地址字典
        self.firmware_paths = {}
        self.firmware_widgets = {}
        self.flash_thread = None
        self.apply_onsen_theme()
        self.configure_connection_selectors(self.device_combo.currentText())
        self.set_console_ready(False)

    def apply_onsen_theme(self):
        """应用 Onsen UI 风格的浅色页面、列表卡片与蓝色交互主题。"""
        self.setStyleSheet("""
            QMainWindow, QWidget#centralWidget {
                background-color: #f2f2f7;
            }
            QWidget#centralWidget QLabel {
                color: #3a3a3c;
                background: transparent;
            }
            QFrame#brandFrame {
                background-color: #ffffff;
                border: 1px solid #d1d1d6;
                border-radius: 10px;
            }
            QFrame#panelFrame {
                background-color: #ffffff;
                border: 1px solid #d1d1d6;
                border-radius: 10px;
            }
            QFrame#transparentFrame, QFrame#bottomFrame, QWidget#firmwareContent {
                background: transparent;
                border: none;
            }
            QLabel#logoLabel {
                border: none;
                background: transparent;
            }
            QLabel#brandTitleLabel {
                color: #1c1c1e;
                font-size: 22px;
                font-weight: 700;
            }
            QLabel#brandSubtitleLabel {
                color: #0076ff;
                font-size: 11px;
                font-weight: 600;
            }
            QFrame#consoleBadgeFrame {
                background-color: #eef6ff;
                border: 1px solid #cfe5ff;
                border-radius: 14px;
            }
            QLabel#consoleBadge {
                color: #0076ff;
                border: none;
                font-size: 10px;
                font-weight: 700;
            }
            QLabel#readyIndicator {
                color: #c7c7cc;
                border: none;
                font-size: 12px;
            }
            QLabel#sectionTitle {
                color: #1c1c1e;
                font-size: 13px;
                font-weight: 700;
            }
            QLabel#sectionIndex {
                color: #8e8e93;
                font-size: 9px;
                font-weight: 700;
            }
            QLabel#fieldLabel {
                color: #636366;
                font-weight: 600;
            }
            QLabel#statusLabel {
                color: #315b82;
                background-color: #f0f7ff;
                border: 1px solid #cfe5ff;
                border-left: 4px solid #0076ff;
                border-radius: 6px;
                padding: 8px 11px;
                font-weight: 600;
            }
            QLabel#statusLabel[statusKind="success"] {
                color: #23743b;
                background-color: #eefaf1;
                border-color: #ccebd5;
                border-left-color: #34c759;
            }
            QLabel#statusLabel[statusKind="error"] {
                color: #a32633;
                background-color: #fff1f2;
                border-color: #ffd1d6;
                border-left-color: #ff3b30;
            }
            QFrame#deviceDetailFrame {
                background-color: #f7f7fa;
                border: 1px solid #e5e5ea;
                border-radius: 7px;
            }
            QLabel#detailCaption {
                color: #8e8e93;
                font-size: 9px;
                font-weight: 700;
            }
            QLabel#detailStateValue {
                color: #248a3d;
                font-size: 11px;
                font-weight: 700;
            }
            QPushButton#filePathButton {
                color: #3a3a3c;
                background-color: #ffffff;
                border: 1px solid #c7c7cc;
                border-radius: 6px;
                padding: 6px 10px;
                text-align: left;
            }
            QPushButton#filePathButton:hover {
                color: #0062d1;
                background-color: #f7fbff;
                border-color: #0076ff;
            }
            QPushButton#filePathButton:pressed {
                background-color: #e8f3ff;
            }
            QPushButton#clearFileButton {
                color: #8e8e93;
                background-color: #f2f2f7;
                border: 1px solid #d1d1d6;
                border-radius: 6px;
                font-size: 16px;
                font-weight: 600;
            }
            QPushButton#clearFileButton:hover {
                color: #ff3b30;
                background-color: #fff1f2;
                border-color: #ffb8bf;
            }
            QLabel#safetyLabel {
                color: #8e8e93;
                font-size: 10px;
            }
            QLabel#warningIcon {
                color: #ff9500;
                font-size: 12px;
                font-weight: 700;
            }
            QWidget#centralWidget QComboBox,
            QWidget#centralWidget QLineEdit {
                color: #1c1c1e;
                background-color: #ffffff;
                border: 1px solid #c7c7cc;
                border-radius: 6px;
                padding: 6px 10px;
                selection-color: #ffffff;
                selection-background-color: #0076ff;
            }
            QWidget#centralWidget QComboBox:hover,
            QWidget#centralWidget QLineEdit:hover {
                border-color: #8e8e93;
            }
            QWidget#centralWidget QComboBox:focus,
            QWidget#centralWidget QLineEdit:focus {
                border: 1px solid #0076ff;
            }
            QWidget#centralWidget QComboBox:disabled,
            QWidget#centralWidget QLineEdit:disabled {
                color: #aeaeb2;
                background-color: #f2f2f7;
                border-color: #e5e5ea;
            }
            QWidget#centralWidget QComboBox QAbstractItemView {
                color: #1c1c1e;
                background-color: #ffffff;
                border: 1px solid #c7c7cc;
                selection-color: #ffffff;
                selection-background-color: #0076ff;
                outline: none;
            }
            QComboBox#deviceCombo {
                font-size: 14px;
                font-weight: 600;
                padding-left: 12px;
            }
            QComboBox#deviceCombo QAbstractItemView {
                font-size: 14px;
                font-weight: 500;
                padding: 4px;
            }
            QComboBox#deviceCombo QAbstractItemView::item {
                min-height: 30px;
                padding: 3px 10px;
            }
            QComboBox#portCombo, QComboBox#baudrateCombo {
                color: #3a3a3c;
                background-color: transparent;
                border: 1px solid transparent;
                border-radius: 4px;
                font-size: 11px;
                font-weight: 600;
                padding: 1px 22px 1px 2px;
            }
            QComboBox#portCombo:hover, QComboBox#baudrateCombo:hover,
            QComboBox#portCombo:focus, QComboBox#baudrateCombo:focus {
                color: #0062d1;
                background-color: #e8f3ff;
                border-color: #80baff;
            }
            QComboBox#portCombo:disabled, QComboBox#baudrateCombo:disabled {
                color: #aeaeb2;
                background-color: transparent;
                border-color: transparent;
            }
            QComboBox#portCombo QAbstractItemView,
            QComboBox#baudrateCombo QAbstractItemView {
                font-size: 14px;
                font-weight: 500;
                padding: 4px;
            }
            QComboBox#portCombo QAbstractItemView::item,
            QComboBox#baudrateCombo QAbstractItemView::item {
                min-height: 30px;
                padding: 3px 10px;
            }
            QComboBox#chipCombo {
                padding-right: 28px;
                font-size: 11px;
                font-weight: 600;
            }
            QFrame#logControlFrame {
                background-color: #ffffff;
                border: 1px solid #d1d1d6;
                border-radius: 7px;
            }
            QLineEdit#logFilterInput {
                min-height: 20px;
                border-color: transparent;
                background-color: transparent;
                color: #3a3a3c;
            }
            QLineEdit#logFilterInput:focus {
                border-color: transparent;
            }
            QPushButton#logClearButton {
                color: #0076ff;
                background-color: #eef6ff;
                border: 1px solid #cfe5ff;
                border-radius: 5px;
                font-size: 10px;
                font-weight: 600;
            }
            QPushButton#logClearButton:hover {
                color: #005ecb;
                border-color: #80baff;
                background-color: #e0efff;
            }
            QPushButton#secondaryButton {
                color: #0076ff;
                background-color: #ffffff;
                border: 1px solid #0076ff;
                border-radius: 6px;
                padding: 6px 12px;
                font-weight: 600;
            }
            QPushButton#secondaryButton:hover {
                color: #005ecb;
                background-color: #eef6ff;
                border-color: #005ecb;
            }
            QPushButton#secondaryButton:pressed {
                color: #ffffff;
                background-color: #0076ff;
            }
            QPushButton#primaryButton {
                color: #ffffff;
                background-color: #0076ff;
                border: 1px solid #0076ff;
                border-radius: 8px;
                padding: 8px 18px;
                font-size: 13px;
                font-weight: 700;
            }
            QPushButton#primaryButton:hover {
                background-color: #1989fa;
                border-color: #1989fa;
            }
            QPushButton#primaryButton:pressed {
                background-color: #005ecb;
                border-color: #005ecb;
            }
            QPushButton#primaryButton:disabled,
            QPushButton#secondaryButton:disabled {
                color: #aeaeb2;
                background-color: #e5e5ea;
                border-color: #d1d1d6;
            }
            QTextEdit#consoleOutput {
                color: #3a3a3c;
                background-color: #ffffff;
                border: 1px solid #d1d1d6;
                border-radius: 8px;
                padding: 10px;
                font-size: 11px;
                selection-color: #ffffff;
                selection-background-color: #0076ff;
            }
            QTextEdit#consoleOutput:focus {
                border-color: #80baff;
            }
            QProgressBar#flashProgress {
                background-color: #e5e5ea;
                border: none;
                border-radius: 2px;
            }
            QProgressBar#flashProgress::chunk {
                background-color: #0076ff;
                border-radius: 2px;
            }
            QScrollBar:vertical {
                background-color: transparent;
                width: 10px;
                margin: 2px;
            }
            QScrollBar::handle:vertical {
                background-color: #c7c7cc;
                min-height: 28px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical:hover {
                background-color: #aeaeb2;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                background: transparent;
                border: none;
                height: 0;
            }
            QToolTip {
                color: #ffffff;
                background-color: #3a3a3c;
                border: 1px solid #1c1c1e;
                padding: 5px;
            }
        """)

    def set_console_ready(self, ready):
        """根据当前设备配置是否就绪启动或停止顶部脉动指示器。"""
        if ready:
            self._pulse_visible = True
            if not self.ready_pulse_timer.isActive():
                self.ready_pulse_timer.start()
            self.ready_indicator.setToolTip("设备配置与连接参数已就绪")
            self.toggle_ready_pulse()
            return

        self.ready_pulse_timer.stop()
        self._pulse_visible = False
        self.ready_indicator.setStyleSheet(
            "color: #c7c7cc; background: transparent; border: none;"
        )
        self.ready_indicator.setToolTip("等待设备配置就绪")

    def toggle_ready_pulse(self):
        """在两档绿色亮度之间切换，形成低干扰的呼吸反馈。"""
        self._pulse_visible = not self._pulse_visible
        color = "#34c759" if self._pulse_visible else "#a8dcb5"
        self.ready_indicator.setStyleSheet(
            f"color: {color}; background: transparent; border: none;"
        )

    def update_device_details(self, device_name):
        """根据状态栏选择器刷新设备配置就绪状态。"""
        if device_name not in self.device_configs:
            self.detail_state_value.setText("等待选择")
            self.set_console_ready(False)
            return

        config = self.device_configs[device_name]
        if config["flash_tool"] == "JLink":
            self.detail_state_value.setText("配置就绪")
            self.set_console_ready(True)
            return

        port = self.port_combo.currentText().strip()
        self.detail_state_value.setText("配置就绪" if port else "等待串口")
        self.set_console_ready(bool(port))

    @staticmethod
    def replace_selector_items(selector, items, selected_text=None, placeholder="--"):
        """原子替换状态栏选择项，并避免中间状态触发就绪判断。"""
        selector.blockSignals(True)
        selector.clear()
        selector.setPlaceholderText(placeholder)
        selector.addItems(items)
        if selected_text and selector.findText(selected_text) >= 0:
            selector.setCurrentText(selected_text)
        elif items:
            selector.setCurrentIndex(0)
        elif not items:
            selector.setCurrentIndex(-1)
        selector.blockSignals(False)

    def configure_connection_selectors(self, device_name):
        """按烧录工具配置状态栏中的端口、接口和速率选择项。"""
        if device_name not in self.device_configs:
            self.replace_selector_items(self.port_combo, [], placeholder="--")
            self.replace_selector_items(self.baudrate_combo, [], placeholder="--")
            self.port_combo.setEnabled(False)
            self.baudrate_combo.setEnabled(False)
            return

        config = self.device_configs[device_name]
        self.port_combo.setEnabled(True)
        self.baudrate_combo.setEnabled(True)
        if config["flash_tool"] == "JLink":
            self.replace_selector_items(
                self.port_combo, ["J-Link / SWD"], "J-Link / SWD"
            )
            self.replace_selector_items(self.baudrate_combo, ["4 MHz"], "4 MHz")
            return

        default_baudrate = config.get("baudrate", "115200")
        baudrate_options = list(BAUDRATE_OPTIONS)
        if default_baudrate not in baudrate_options:
            baudrate_options.append(default_baudrate)
        self.replace_selector_items(
            self.baudrate_combo, baudrate_options, default_baudrate
        )
        self.update_available_ports()

    @staticmethod
    def firmware_placeholder(firmware_type):
        """返回由明确分区名称和文件选择动作组成的占位文本。"""
        return f"{firmware_type.capitalize()} · 点击选择文件"

    @staticmethod
    def compact_file_name(file_path, maximum_length=38):
        """保留文件扩展名并截断过长名称，完整路径通过工具提示提供。"""
        file_name = Path(file_path).name
        if len(file_name) <= maximum_length:
            return file_name
        suffix = Path(file_name).suffix
        prefix_length = max(8, maximum_length - len(suffix) - 2)
        return f"{file_name[:prefix_length]}…{suffix}"

    def clear_firmware_selection(self, firmware_type):
        """清除指定分区的已选文件并恢复文件占位提示。"""
        self.firmware_paths[firmware_type] = None
        widgets = self.firmware_widgets.get(firmware_type, {})
        file_button = widgets.get("file_button")
        clear_button = widgets.get("clear_button")
        if file_button:
            file_button.setText(self.firmware_placeholder(firmware_type))
            file_button.setIcon(
                self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon)
            )
            file_button.setToolTip("点击选择固件文件")
        if clear_button:
            clear_button.setVisible(False)

    def handle_select_firmware(self, firmware_type: str, file_button: QPushButton):
        """打开文件对话框并在按钮中显示截断后的固件文件名。"""
        dialog = QFileDialog(self)
        dialog.setWindowTitle(f"选择{firmware_type}文件")
        dialog.setNameFilter("Firmware Files (*.hex *.bin);;All Files (*.*)")
        dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
        
        if dialog.exec() == QFileDialog.DialogCode.Accepted:
            files = dialog.selectedFiles()
            if files:
                file_path = files[0]
                self.firmware_paths[firmware_type] = file_path
                file_button.setText(self.compact_file_name(file_path))
                file_button.setIcon(
                    self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon)
                )
                file_button.setToolTip(file_path)
                clear_button = self.firmware_widgets[firmware_type]["clear_button"]
                clear_button.setVisible(True)

    def handle_flash_button(self):
        """处理烧录按钮点击事件"""
        try:
            current_device = self.device_combo.currentText()
            if current_device not in self.device_configs:
                self.update_status("请先选择设备类型")
                return

            flash_tool = self.device_configs[current_device]["flash_tool"]
            firmware_inputs = self.get_firmware_inputs()
            firmware_paths = {
                firmware_type: self.firmware_paths.get(firmware_type)
                for firmware_type in firmware_inputs
            }
            port = self.port_combo.currentText().strip()
            baudrate = self.baudrate_combo.currentText().strip()

            validation_error = FlashThread.validate_job(
                flash_tool, firmware_paths, firmware_inputs, port, baudrate
            )
            if validation_error:
                self.update_status(validation_error)
                return

            jlink_library_path = None
            if flash_tool == "JLink":
                jlink_library_path = self.prepare_jlink_environment()
                if not jlink_library_path:
                    return

            self.flash_button.setEnabled(False)
            self.device_combo.setEnabled(False)
            self.clear_log()
            self.flash_progress.setVisible(True)
            self.flash_thread = FlashThread(
                flash_tool,
                current_device,
                firmware_paths,
                firmware_inputs,
                port,
                baudrate,
                jlink_library_path,
            )
            self.flash_thread.progress.connect(self.update_status)
            self.flash_thread.log.connect(self.append_log)
            self.flash_thread.completed.connect(self.on_flash_complete)
            self.flash_thread.start()
        except Exception as exc:
            self.flash_button.setEnabled(True)
            self.device_combo.setEnabled(True)
            self.flash_progress.setVisible(False)
            self.update_status(f"无法开始烧录: {exc}")

    def on_flash_complete(self, success: bool, message: str):
        """烧录完成回调"""
        self.flash_button.setEnabled(True)
        self.device_combo.setEnabled(True)
        self.flash_progress.setVisible(False)
        self.append_log(message)
        if success:
            self.update_status("烧录成功")
        else:
            self.update_status(f"烧录失败\n{message}")

    def update_status(self, message: str):
        """完整显示状态文本，并同步提供可悬浮查看的内容。"""
        message = str(message).strip()
        success = message.startswith("已加载") or message == "烧录成功"
        error_terms = (
            "失败",
            "错误",
            "无效",
            "不存在",
            "未检测到",
            "无法",
            "缺少",
        )
        status_kind = "success" if success else "info"
        if any(term in message for term in error_terms):
            status_kind = "error"
        display_message = f"✓  {message}" if success else message
        self.status_label.setProperty("statusKind", status_kind)
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)
        self.status_label.setText(display_message)
        self.status_label.setToolTip(message)
        self.status_label.updateGeometry()
        self.top_frame.updateGeometry()

    def append_log(self, message: str):
        """保存一条日志并根据当前过滤条件重新渲染彩色终端内容。"""
        message = str(message).strip()
        if message:
            self.log_messages.append(message)
            self.render_log()

    def clear_log(self):
        """清空完整日志缓存和当前终端显示。"""
        self.log_messages.clear()
        self.text_edit.clear()

    def render_log(self, _filter_text=None):
        """按搜索词过滤日志，并对信息、警告和错误行进行分级着色。"""
        filter_text = self.log_filter_input.text().strip().casefold()
        rendered_lines = []
        for message in self.log_messages:
            for line in message.splitlines() or [message]:
                if filter_text and filter_text not in line.casefold():
                    continue
                normalized = line.casefold()
                if any(term in normalized for term in ("error", "失败", "错误")):
                    color = "#ff3b30"
                elif any(term in normalized for term in ("warning", "warn", "警告")):
                    color = "#c76b00"
                elif any(
                    term in normalized
                    for term in ("info", "信息", "正在", "已连接", "完成", "成功")
                ):
                    color = "#248a3d"
                else:
                    color = "#3a3a3c"
                escaped_line = html.escape(line)
                rendered_lines.append(
                    f'<span style="color:{color};">{escaped_line}</span>'
                )

        self.text_edit.setHtml(
            '<div style="font-size: 11px; line-height: 1.55;">'
            + "<br>".join(rendered_lines)
            + "</div>"
        )
        scroll_bar = self.text_edit.verticalScrollBar()
        scroll_bar.setValue(scroll_bar.maximum())

    def prepare_jlink_environment(self):
        """在启动 JLink 烧录前检查运行库和设备配置。"""
        jlink_library_path = self.get_jlink_library_path()
        if not jlink_library_path:
            self.show_error_and_exit()
            return None
        if not self.copy_st_device_configs():
            self.show_copy_error_and_exit()
            return None
        return str(jlink_library_path)

    def get_jlink_library_path(self):
        """返回当前平台随应用分发的 JLink 动态库路径。"""
        import platform

        system = platform.system()
        if system == "Windows":
            filename = "JLink_x64.dll" if sys.maxsize > 2**32 else "JLinkARM.dll"
            library_path = Path(resource_path(f"bin/win/{filename}"))
        elif system == "Linux":
            library_path = Path(resource_path("bin/linux/libjlinkarm.so"))
        elif system == "Darwin":
            library_path = Path(resource_path("bin/mac/libjlinkarm.dylib"))
        else:
            return None
        return library_path if library_path.is_file() else None

    def on_device_changed(self, device_name: str):
        """加载目标设备配置，并确保选择完成后下拉菜单保持关闭。"""
        QTimer.singleShot(0, self.device_combo.hidePopup)
        # 清除之前的按钮
        self.clear_firmware_buttons()
        self.firmware_paths = {}
        self.firmware_widgets = {}
        
        if device_name == "请选择设备":
            self.configure_connection_selectors(device_name)
            self.update_status("系统待命 · 请选择目标设备")
            self.update_device_details(device_name)
            return
        
        if device_name not in self.device_configs:
            self.configure_connection_selectors(device_name)
            self.update_status("设备配置不存在")
            self.update_device_details(device_name)
            return

        firmware_configs = self.device_configs[device_name]['firmware_configs']
        self.firmware_paths = {firmware_type: None for firmware_type in firmware_configs}
        
        # 为每个固件配置生成按钮
        for firmware_type in firmware_configs:
            self.add_firmware_button(firmware_type)
        self.update_status(f"已加载 {device_name} · 等待选择固件")
        
        if self.device_configs[device_name]['flash_tool'] == 'esptool':
            try:
                self.configure_connection_selectors(device_name)
            except Exception as exc:
                self.replace_selector_items(
                    self.port_combo, [], placeholder="未检测到串口"
                )
                self.update_status(f"串口枚举失败: {exc}")
        else:
            self.configure_connection_selectors(device_name)
        self.update_device_details(device_name)
    
    def clear_firmware_buttons(self):
        """
        Clear all firmware selection buttons
        """
        while self.firmware_buttons_layout.count():
            item = self.firmware_buttons_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.firmware_widgets = {}
    
    def add_firmware_button(self, firmware_type: str):
        """添加可点击文件占位、清除操作、地址和芯片选择器。"""
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)  # 设置控件之间的间距
        
        # 文件区域本身可点击，选择后通过旁边的 X 快速清除。
        file_button = QPushButton(self.firmware_placeholder(firmware_type))
        file_button.setObjectName("filePathButton")
        file_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon)
        )
        file_button.setIconSize(QSize(16, 16))
        file_button.setFixedHeight(36)
        file_button.setMinimumWidth(240)
        file_button.setToolTip("点击选择固件文件")
        file_button.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        file_button.clicked.connect(
            lambda _checked=False: self.handle_select_firmware(
                firmware_type, file_button
            )
        )
        clear_button = QPushButton("×")
        clear_button.setObjectName("clearFileButton")
        clear_button.setFixedSize(34, 36)
        clear_button.setToolTip("清除已选文件")
        clear_button.setVisible(False)
        clear_button.clicked.connect(
            lambda _checked=False: self.clear_firmware_selection(firmware_type)
        )
        
        # 地址标签和输入框
        address_label = QLabel("地址")
        address_label.setObjectName("fieldLabel")
        address_label.setFixedHeight(36)
        
        address_input = QLineEdit()
        address_input.setFixedWidth(120)
        address_input.setFixedHeight(36)
        
        # 设备名称标签和输入框
        device_label = QLabel("芯片")
        device_label.setObjectName("fieldLabel")
        device_label.setFixedHeight(36)
        
        device_input = QComboBox()
        device_input.setObjectName("chipCombo")
        device_input.setEditable(True)
        device_input.setFixedWidth(160)
        device_input.setFixedHeight(36)
        chip_names = sorted(
            {
                firmware_config["device_name"]
                for device_config in self.device_configs.values()
                for firmware_config in device_config["firmware_configs"].values()
                if firmware_config.get("device_name")
            }
        )
        device_input.addItems(chip_names)
        
        # 设置默认值
        current_device = self.device_combo.currentText()
        if current_device in self.device_configs:
            config = self.device_configs[current_device]['firmware_configs'].get(firmware_type, {})
            if config:
                address_input.setText(config['address'])
                device_input.setCurrentText(config['device_name'])
        
        # 添加所有控件到布局
        layout.addWidget(file_button)
        layout.addWidget(clear_button)
        layout.addWidget(address_label)
        layout.addWidget(address_input)
        layout.addWidget(device_label)
        layout.addWidget(device_input)
        layout.addStretch()
        
        self.firmware_buttons_layout.addWidget(container)
        self.firmware_widgets[firmware_type] = {
            "address": address_input,
            "device_name": device_input,
            "file_button": file_button,
            "clear_button": clear_button,
        }

    def get_firmware_inputs(self) -> dict:
        """
        Get all firmware inputs including address and device name
        Returns:
            Dictionary of firmware_type: {'address': str, 'device_name': str}
        """
        inputs = {}
        for firmware_type, widgets in self.firmware_widgets.items():
            inputs[firmware_type] = {
                'address': widgets["address"].text().strip(),
                'device_name': widgets["device_name"].currentText().strip(),
            }
        return inputs

    def show_error_and_exit(self):
        """提示当前安装包缺少对应平台的 JLink 动态库。"""
        QMessageBox.critical(
            self,
            "JLink 环境错误",
            "安装包中缺少当前平台的 JLink 动态库，无法启动 JLink 烧录。\n"
            "请检查 bin 目录和打包配置。WLED/esptool 烧录不受影响。",
        )

    def copy_st_device_configs(self) -> bool:
        """检查 Devices.xml，并在版本变化时同步整套 ST 资源。"""
        import platform

        try:
            source_dir = Path(resource_path("bin/ST"))
            system = platform.system()
            if system == "Windows":
                app_data = os.environ.get("APPDATA")
                if not app_data:
                    raise RuntimeError("APPDATA 环境变量不存在")
                target_dir = Path(app_data) / "SEGGER/JLinkDevices"
            elif system == "Linux":
                target_dir = Path.home() / ".config/SEGGER/JLinkDevices"
            elif system == "Darwin":
                target_dir = Path.home() / "Library/Application Support/SEGGER/JLinkDevices"
            else:
                raise RuntimeError(f"不支持的操作系统: {system}")

            target_st_dir = target_dir / "ST"
            updated = self.synchronize_st_resources(source_dir, target_st_dir)
            if updated:
                self.append_log(f"ST 设备资源已更新: {target_st_dir}")
            else:
                self.append_log("ST/Devices.xml 与内置资源一致，无需更新。")
            return True
        except Exception as exc:
            self.append_log(f"同步 ST 设备资源失败: {exc}")
            return False

    @staticmethod
    def synchronize_st_resources(source_dir, target_st_dir):
        """按 Devices.xml 判断版本，并以可回滚方式替换整个 ST 目录。"""
        source_dir = Path(source_dir)
        target_st_dir = Path(target_st_dir)
        source_devices = source_dir / "Devices.xml"
        target_devices = target_st_dir / "Devices.xml"

        if not source_dir.is_dir():
            raise FileNotFoundError(f"ST 资源目录不存在: {source_dir}")
        if not source_devices.is_file():
            raise FileNotFoundError(f"ST 资源缺少 Devices.xml: {source_devices}")
        if target_devices.is_file() and filecmp.cmp(
            source_devices,
            target_devices,
            shallow=False,
        ):
            return False

        target_st_dir.parent.mkdir(parents=True, exist_ok=True)
        workspace = Path(
            tempfile.mkdtemp(prefix=".st-resource-update-", dir=target_st_dir.parent)
        )
        staged_st_dir = workspace / "ST"
        backup_st_dir = workspace / "previous-ST"
        old_target_moved = False
        replacement_started = False
        try:
            shutil.copytree(source_dir, staged_st_dir)
            if not filecmp.cmp(
                source_devices,
                staged_st_dir / "Devices.xml",
                shallow=False,
            ):
                raise OSError("暂存目录中的 Devices.xml 校验失败")

            if target_st_dir.exists():
                os.replace(target_st_dir, backup_st_dir)
                old_target_moved = True
            replacement_started = True
            os.replace(staged_st_dir, target_st_dir)

            if not filecmp.cmp(
                source_devices,
                target_st_dir / "Devices.xml",
                shallow=False,
            ):
                raise OSError("目标目录中的 Devices.xml 校验失败")
            return True
        except Exception:
            if replacement_started and target_st_dir.exists():
                shutil.rmtree(target_st_dir)
            if old_target_moved and backup_st_dir.exists():
                os.replace(backup_st_dir, target_st_dir)
            raise
        finally:
            shutil.rmtree(workspace, ignore_errors=True)

    def show_copy_error_and_exit(self):
        """
        Show error message for ST config copy failure and exit application
        """
        from PyQt6.QtWidgets import QMessageBox
        import platform
        
        system = platform.system()
        if system == "Windows":
            target_path = "%APPDATA%\\SEGGER\\JLinkDevices"
        elif system == "Linux":
            target_path = "$HOME/.config/SEGGER/JLinkDevices"
        else:  # macOS
            target_path = "$HOME/Library/Application Support/SEGGER/JLinkDevices"
        
        msg = QMessageBox()
        msg.setIcon(QMessageBox.Icon.Critical)
        msg.setWindowTitle("配置文件错误")
        msg.setText("复制ST设备配置文件失败")
        msg.setInformativeText(f"""
无法复制ST设备配置文件到JLink设备目录。

请检查:
1. 当前目录下是否存在ST文件夹
2. 是否有权限访问目标目录
3. 磁盘空间是否充足

目标目录:
{target_path}
        """)
        msg.setStandardButtons(QMessageBox.StandardButton.Ok)
        msg.buttonClicked.connect(lambda: self.close())
        msg.exec()

    def closeEvent(self, event):
        """处理窗口关闭事件"""
        if self.has_active_operation():
            QMessageBox.warning(
                self,
                "烧录进行中",
                "烧录尚未结束。为避免设备或串口损坏，请等待烧录完成后再关闭窗口。",
            )
            event.ignore()
            return
        event.accept()

    def has_active_operation(self) -> bool:
        """返回当前是否存在尚未退出的烧录线程。"""
        return bool(self.flash_thread and self.flash_thread.isRunning())

    def update_available_ports(self):
        """刷新状态栏串口选择项并尽量保留当前选择。"""
        selected_port = self.port_combo.currentText().strip()
        ports = list_ports.comports()
        port_names = [port.device for port in ports]
        selected_port = selected_port if selected_port in port_names else None
        self.replace_selector_items(
            self.port_combo,
            port_names,
            selected_port,
            placeholder="未检测到串口",
        )
        self.update_device_details(self.device_combo.currentText())

class FlashThread(QThread):
    """在工作线程中执行固件烧录并统一报告结果。"""

    progress = pyqtSignal(str)
    log = pyqtSignal(str)
    completed = pyqtSignal(bool, str)

    def __init__(
        self,
        flash_tool,
        device_name,
        firmware_paths,
        firmware_inputs,
        port,
        baudrate,
        jlink_library_path=None,
    ):
        super().__init__()
        self.flash_tool = flash_tool
        self.device_name = device_name
        self.firmware_paths = dict(firmware_paths)
        self.firmware_inputs = {
            key: dict(value) for key, value in firmware_inputs.items()
        }
        self.port = port
        self.baudrate = baudrate
        self.jlink_library_path = jlink_library_path
        self.jlink = None
        self.jlink_serial = None
        self.jlink_messages = []

    @staticmethod
    def validate_job(flash_tool, firmware_paths, firmware_inputs, port, baudrate):
        """校验烧录任务，返回首个可读错误信息或空字符串。"""
        selected = [
            (firmware_type, path)
            for firmware_type, path in firmware_paths.items()
            if path
        ]
        if not selected:
            return "未选择任何固件文件"

        for firmware_type, path in selected:
            file_path = Path(path)
            if not file_path.exists() or not file_path.is_file():
                return f"{firmware_type} 固件文件不存在: {file_path}"
            if not os.access(file_path, os.R_OK):
                return f"{firmware_type} 固件文件不可读: {file_path}"

            firmware_info = firmware_inputs.get(firmware_type)
            if not firmware_info:
                return f"缺少 {firmware_type} 的烧录配置"
            address_text = str(firmware_info.get("address", "")).strip()
            try:
                address = int(address_text, 0)
            except (TypeError, ValueError):
                return f"{firmware_type} 烧录地址无效: {address_text or '空'}"
            if address < 0:
                return f"{firmware_type} 烧录地址不能为负数"
            if flash_tool == "JLink" and not firmware_info.get("device_name", "").strip():
                return f"{firmware_type} 缺少 JLink 设备名称"

        if flash_tool == "esptool":
            if not port:
                return "未检测到可用串口，请连接设备后重新选择"
            try:
                baudrate_value = int(baudrate, 10)
            except (TypeError, ValueError):
                return f"波特率无效: {baudrate or '空'}"
            if not 1200 <= baudrate_value <= 4_000_000:
                return "波特率必须在 1200 到 4000000 之间"
        elif flash_tool != "JLink":
            return f"不支持的烧录工具: {flash_tool}"
        return ""

    def run(self):
        """执行经过校验的烧录任务，并保证资源清理和单次结果通知。"""
        success = False
        message = "烧录未执行"
        try:
            validation_error = self.validate_job(
                self.flash_tool,
                self.firmware_paths,
                self.firmware_inputs,
                self.port,
                self.baudrate,
            )
            if validation_error:
                message = validation_error
            elif self.isInterruptionRequested():
                message = "烧录已取消"
            elif self.flash_tool == "JLink":
                success, message = self.connect_jlink()
                if success:
                    success, message = self.run_jlink_flash()
            elif self.flash_tool == "esptool":
                success, message = self.run_esptool_flash()
            else:
                message = f"不支持的烧录工具: {self.flash_tool}"
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
            message = f"烧录工具异常退出，退出码: {code}"
        except Exception as e:
            message = f"烧录失败: {e}"
        finally:
            self.close_jlink()
            self.completed.emit(success, message)

    def connect_jlink(self) -> Tuple[bool, str]:
        """
        Connect to JLink device
        Returns:
            Tuple of (success, message)
        """
        try:
            if not self.jlink_library_path:
                return False, "未配置 JLink 动态库"
            library = pylink.library.Library(self.jlink_library_path)
            self.jlink = pylink.JLink(
                lib=library,
                log=lambda message: self.handle_jlink_log("信息", message),
                detailed_log=lambda message: self.handle_jlink_log("详细", message),
                warn=lambda message: self.handle_jlink_log("警告", message),
                error=lambda message: self.handle_jlink_log("错误", message),
            )

            self.emit_jlink_stage("正在枚举 J-Link 探针...")
            emulators = self.jlink.connected_emulators()
            if not emulators:
                return False, "未检测到 J-Link 探针，请检查 USB 连接和驱动。"
            if len(emulators) > 1:
                serial_numbers = ", ".join(
                    str(emulator.SerialNumber) for emulator in emulators
                )
                return False, f"检测到多个 J-Link 探针，请只保留一个。序列号: {serial_numbers}"

            self.jlink_serial = int(emulators[0].SerialNumber)
            self.emit_jlink_stage(f"正在打开 J-Link 探针 {self.jlink_serial}...")
            self.jlink.open(serial_no=self.jlink_serial)
            self.emit_jlink_stage(f"J-Link 探针 {self.jlink_serial} 已连接")
            return True, "Connected to JLink successfully"
        except Exception as e:
            return False, self.describe_jlink_error("打开探针", e)

    @staticmethod
    @contextmanager
    def jlink_firmware_path(firmware_path):
        """为 J-Link 提供 ASCII 绝对路径，退出时清理 Unicode 固件的临时副本。

        Python 负责读取原文件，避免 DLL 解释 UTF-8 路径失败。临时目录
        本身也必须为 ASCII；Windows 中文用户名下尝试公共目录和系统临时目录。
        保留扩展名，使 HEX 文件仍由 J-Link 解析地址记录，不作为裸二进制写入。
        """
        source = Path(firmware_path).resolve()
        if str(source).isascii():
            yield str(source)
            return

        candidates = [Path(tempfile.gettempdir())]
        if os.name == "nt":
            public = os.environ.get("PUBLIC")
            system_root = os.environ.get("SystemRoot")
            if public:
                candidates.extend([Path(public) / "Documents", Path(public)])
            if system_root:
                candidates.append(Path(system_root) / "Temp")
        candidates.extend([Path(sys.executable).parent, Path.cwd()])
        temporary = None
        for candidate in dict.fromkeys(candidates):
            if not str(candidate.resolve()).isascii():
                continue
            try:
                temporary = tempfile.TemporaryDirectory(
                    prefix="hc-flash-", dir=str(candidate.resolve())
                )
                break
            except OSError:
                continue
        if temporary is None:
            raise OSError(
                "无法创建纯英文路径的烧录临时目录，请将 TEMP 设置为可写的纯英文目录"
            )
        with temporary as directory:
            suffix = source.suffix if source.suffix.isascii() else ".bin"
            staged_path = Path(directory) / ("firmware" + suffix)
            shutil.copyfile(source, staged_path)
            yield str(staged_path)

    def run_jlink_flash(self):
        """使用已打开的 JLink 连接烧录所有选中的固件。"""
        stage = "检查探针连接"
        try:
            if not self.jlink or not self.jlink.connected():
                return False, "JLink未连接"

            connected_device = None
            for firmware_type, firmware_info in self.firmware_inputs.items():
                firmware_path = self.firmware_paths.get(firmware_type)
                if not firmware_path:
                    continue
                if self.isInterruptionRequested():
                    return False, "烧录已取消"

                device_name = firmware_info["device_name"]
                if connected_device != device_name:
                    stage = "选择 SWD 接口"
                    self.emit_jlink_stage("正在选择 SWD 接口...")
                    if not self.jlink.set_tif(pylink.enums.JLinkInterfaces.SWD):
                        return False, "J-Link 无法切换到 SWD 接口，请检查探针型号和连接。"

                    stage = "检查目标供电"
                    try:
                        target_voltage_mv = self.jlink.hardware_status.voltage
                        self.log.emit(f"目标电压: {target_voltage_mv / 1000:.3f} V")
                        if target_voltage_mv < 500:
                            return False, "目标板未供电或 VTref 未连接，请检查电源和 J-Link VTref 引脚。"
                    except Exception as voltage_error:
                        self.log.emit(f"无法读取目标电压: {voltage_error}")

                    stage = f"连接目标设备 {device_name}"
                    self.emit_jlink_stage(f"正在连接目标设备 {device_name}...")
                    self.jlink.connect(device_name, speed=4000, verbose=True)
                    connected_device = device_name

                stage = f"停止目标设备 {device_name}"
                self.jlink.halt()

                address = int(firmware_info["address"], 0)
                stage = f"烧录 {firmware_type} 到 {firmware_info['address']}"
                self.emit_jlink_stage(
                    f"正在烧录 {firmware_type} 到 {firmware_info['address']}..."
                )
                with self.jlink_firmware_path(firmware_path) as download_path:
                    bytes_flashed = self.jlink.flash_file(download_path, address)
                self.log.emit(f"{firmware_type} 已写入 {bytes_flashed} 字节")

                stage = f"复位目标设备 {device_name}"
                self.jlink.reset()
                self.jlink.restart()
            return True, "烧录完成"
        except Exception as exc:
            return False, self.describe_jlink_error(stage, exc)

    def emit_jlink_stage(self, message):
        """同时向状态区和日志区发送 JLink 阶段信息。"""
        self.progress.emit(message)
        self.log.emit(message)

    def handle_jlink_log(self, level, message):
        """过滤并保存 JLink DLL 回调信息。"""
        message = str(message).strip()
        if not message:
            return
        self.jlink_messages.append(message)
        self.jlink_messages = self.jlink_messages[-50:]
        if level != "详细" or any(
            keyword in message.lower()
            for keyword in ("error", "failed", "cannot", "voltage", "target")
        ):
            self.log.emit(f"JLink {level}: {message}")

    def describe_jlink_error(self, stage, error):
        """把 JLink SDK 的模糊异常转换为包含失败阶段的可操作提示。"""
        error_text = str(error).strip() or type(error).__name__
        combined_text = " ".join([error_text, *self.jlink_messages]).lower()
        serial_suffix = f"（序列号 {self.jlink_serial}）" if self.jlink_serial else ""

        if stage == "打开探针" and any(
            text in combined_text
            for text in ("cannot connect", "already open", "unspecified error")
        ):
            return (
                f"无法打开 J-Link 探针{serial_suffix}。探针可能被其他程序占用；"
                "请关闭 J-Link Commander、Ozone、IDE 调试会话和其他烧录助手，"
                "必要时重新插拔探针后重试。"
            )
        if "no power" in combined_text or "vcc" in combined_text:
            return "目标板未供电或 VTref 未连接，请检查目标电源与调试线。"
        if "no cpu" in combined_text or "could not find supported cpu" in combined_text:
            return f"{stage}失败：未检测到目标芯片，请检查芯片型号、SWD 接线和目标供电。"
        if "target interface" in combined_text:
            return f"{stage}失败：SWD 接口不可用，请检查探针固件和接口配置。"
        return f"JLink 在“{stage}”阶段失败：{error_text}"

    def run_esptool_flash(self):
        """调用 esptool 烧录固件，并将退出行为转换为普通失败结果。"""
        output_stream = SignalTextStream(self.log)
        try:
            with _ESPTOOL_OUTPUT_LOCK, redirect_stdout(output_stream), redirect_stderr(output_stream):
                for firmware_type, firmware_info in self.firmware_inputs.items():
                    firmware_path = self.firmware_paths.get(firmware_type)
                    if not firmware_path:
                        continue
                    if self.isInterruptionRequested():
                        return False, "烧录已取消"

                    self.progress.emit(f"正在使用 esptool 烧录 {firmware_type}...")
                    esptool_args = [
                        "--chip",
                        firmware_info.get("device_name", "ESP8266").lower(),
                        "--port",
                        self.port,
                        "--baud",
                        self.baudrate,
                        self.get_esptool_write_command(),
                        firmware_info["address"],
                        firmware_path,
                    ]
                    esptool.main(esptool_args)
            return True, "烧录完成"
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
            return False, f"esptool参数或执行错误，退出码: {code}"
        except Exception as exc:
            return False, f"esptool烧录失败: {exc}"
        finally:
            output_stream.flush()

    @staticmethod
    def get_esptool_write_command():
        """根据运行时 esptool 主版本返回兼容的写入命令。"""
        version_text = str(getattr(esptool, "__version__", ""))
        try:
            major_version = int(version_text.split(".", 1)[0])
        except (TypeError, ValueError):
            major_version = 4
        return "write-flash" if major_version >= 5 else "write_flash"

    def close_jlink(self):
        """关闭工作线程持有的 JLink 句柄。"""
        if not self.jlink:
            return
        try:
            self.jlink.close()
        except Exception as exc:
            self.log.emit(f"关闭 JLink 连接失败: {exc}")
        finally:
            self.jlink = None
