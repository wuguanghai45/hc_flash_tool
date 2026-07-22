from PyQt6.QtWidgets import (
    QApplication,
    QMessageBox,
)
from PyQt6.QtGui import QFont
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys
import tempfile
import threading
import traceback
from flash_tool import APP_NAME, FlashToolWindow, load_app_icon


LOGGER = logging.getLogger("tool_remote_debug_assistant")


def configure_logging():
    """配置用户目录下的轮转日志并返回日志文件路径。"""
    preferred_directory = Path.home() / ".tool-remote-debug-assistant" / "logs"
    fallback_directory = Path(tempfile.gettempdir()) / "tool-remote-debug-assistant-logs"
    handler = None
    log_path = fallback_directory / "application.log"
    for log_directory in (preferred_directory, fallback_directory):
        try:
            log_directory.mkdir(parents=True, exist_ok=True)
            log_path = log_directory / "application.log"
            handler = RotatingFileHandler(
                log_path,
                maxBytes=2 * 1024 * 1024,
                backupCount=3,
                encoding="utf-8",
            )
            break
        except OSError:
            continue
    if handler is None:
        handler = logging.StreamHandler(sys.__stderr__)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    LOGGER.setLevel(logging.INFO)
    if not LOGGER.handlers:
        LOGGER.addHandler(handler)
    return log_path


def install_exception_handlers(log_path):
    """记录未捕获异常，并在 Qt 主线程异常时展示可定位的信息。"""
    def handle_exception(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        formatted = "".join(
            traceback.format_exception(exc_type, exc_value, exc_traceback)
        )
        LOGGER.critical("Unhandled exception\n%s", formatted)
        app = QApplication.instance()
        if app and threading.current_thread() is threading.main_thread():
            try:
                QMessageBox.critical(
                    None,
                    "程序错误",
                    f"发生未处理错误，程序已记录日志。\n\n{exc_value}\n\n日志: {log_path}",
                )
            except RuntimeError:
                pass

    def handle_thread_exception(args):
        handle_exception(args.exc_type, args.exc_value, args.exc_traceback)

    sys.excepthook = handle_exception
    threading.excepthook = handle_thread_exception


def main():
    """初始化异常保护和 Qt 应用并启动嵌入式烧录助手。"""
    log_path = configure_logging()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setFont(QFont("Arial", 10))
    app.setWindowIcon(load_app_icon())
    install_exception_handlers(log_path)
    window = FlashToolWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
