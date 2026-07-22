"""Regression tests for firmware flashing error handling."""

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PyQt6.QtGui import QFont, QFontDatabase
from PyQt6.QtWidgets import QApplication, QComboBox, QLabel

from flash_tool import APP_NAME, APP_SUBTITLE, FlashThread, FlashToolWindow


class FlashThreadTests(unittest.TestCase):
    """Verify that invalid input and esptool exits never terminate the app."""

    def setUp(self):
        """Create a readable firmware file and a valid WLED job."""
        self.temp_directory = tempfile.TemporaryDirectory()
        self.firmware_path = Path(self.temp_directory.name) / "wled.bin"
        self.firmware_path.write_bytes(b"test firmware")
        self.paths = {"app": str(self.firmware_path)}
        self.inputs = {
            "app": {"address": "0x00000000", "device_name": "ESP8266"}
        }

    def tearDown(self):
        """Remove the temporary firmware directory."""
        self.temp_directory.cleanup()

    def create_thread(self):
        """Return a FlashThread configured for the temporary WLED image."""
        return FlashThread(
            "esptool",
            "Wled-esp8266",
            self.paths,
            self.inputs,
            "/dev/test-port",
            "921600",
        )

    def test_invalid_address_is_rejected_before_esptool(self):
        """An invalid editable address must be reported without invoking esptool."""
        self.inputs["app"]["address"] = "bad-address"
        error = FlashThread.validate_job(
            "esptool", self.paths, self.inputs, "/dev/test-port", "921600"
        )
        self.assertIn("烧录地址无效", error)

    def test_missing_firmware_is_rejected(self):
        """A file removed after selection must produce a validation error."""
        self.paths["app"] = str(Path(self.temp_directory.name) / "missing.bin")
        error = FlashThread.validate_job(
            "esptool", self.paths, self.inputs, "/dev/test-port", "921600"
        )
        self.assertIn("固件文件不存在", error)

    def test_esptool_system_exit_becomes_failure_result(self):
        """Click/SystemExit from esptool must not escape the worker run method."""
        results = []
        thread = self.create_thread()
        thread.completed.connect(lambda success, message: results.append((success, message)))

        with patch("flash_tool.esptool.main", side_effect=SystemExit(2)):
            thread.run()

        self.assertEqual(1, len(results))
        self.assertFalse(results[0][0])
        self.assertIn("退出码: 2", results[0][1])

    def test_esptool_success_emits_one_success_result(self):
        """Esptool 5 must use the hyphenated command and emit one success result."""
        results = []
        thread = self.create_thread()
        thread.completed.connect(lambda success, message: results.append((success, message)))

        with patch("flash_tool.esptool.__version__", "5.3.1"):
            with patch("flash_tool.esptool.main") as esptool_main:
                thread.run()

        self.assertEqual([(True, "烧录完成")], results)
        arguments = esptool_main.call_args.args[0]
        self.assertIn("write-flash", arguments)
        self.assertNotIn("write_flash", arguments)

    def test_esptool_four_uses_legacy_write_command(self):
        """Esptool 4 must receive write_flash instead of the unsupported alias."""
        thread = self.create_thread()

        with patch("flash_tool.esptool.__version__", "4.8.1"):
            with patch("flash_tool.esptool.main") as esptool_main:
                thread.run()

        arguments = esptool_main.call_args.args[0]
        self.assertIn("write_flash", arguments)
        self.assertNotIn("write-flash", arguments)

    def test_unknown_esptool_version_uses_compatible_legacy_command(self):
        """Unknown esptool builds must fall back to the broadly supported command."""
        with patch("flash_tool.esptool.__version__", "development"):
            self.assertEqual("write_flash", FlashThread.get_esptool_write_command())

    def test_jlink_uses_explicit_bundled_library(self):
        """JLink must load the bundled library without writing a system directory."""
        thread = FlashThread(
            "JLink",
            "ALLCAN-led",
            self.paths,
            self.inputs,
            "",
            "",
            "/application/bin/libjlinkarm.dylib",
        )
        fake_jlink = Mock()
        fake_jlink.connected_emulators.return_value = [
            SimpleNamespace(SerialNumber=601023429)
        ]
        fake_library = object()

        with patch("flash_tool.pylink.library.Library", return_value=fake_library) as library:
            with patch("flash_tool.pylink.JLink", return_value=fake_jlink) as jlink:
                success, _message = thread.connect_jlink()

        self.assertTrue(success)
        library.assert_called_once_with("/application/bin/libjlinkarm.dylib")
        self.assertIs(fake_library, jlink.call_args.kwargs["lib"])
        self.assertTrue(callable(jlink.call_args.kwargs["error"]))
        fake_jlink.open.assert_called_once_with(serial_no=601023429)

    def test_busy_jlink_has_actionable_error(self):
        """A probe lock error must identify likely competing applications."""
        thread = FlashThread(
            "JLink",
            "ALLCAN-led",
            self.paths,
            self.inputs,
            "",
            "",
            "/application/bin/libjlinkarm.dylib",
        )
        fake_jlink = Mock()
        fake_jlink.connected_emulators.return_value = [
            SimpleNamespace(SerialNumber=601023429)
        ]
        fake_jlink.open.side_effect = RuntimeError("Cannot connect to J-Link.")

        with patch("flash_tool.pylink.library.Library", return_value=object()):
            with patch("flash_tool.pylink.JLink", return_value=fake_jlink):
                success, message = thread.connect_jlink()

        self.assertFalse(success)
        self.assertIn("其他程序占用", message)
        self.assertIn("601023429", message)

    def test_unpowered_target_is_reported_before_flash(self):
        """A missing VTref voltage must stop before any flash operation."""
        thread = FlashThread(
            "JLink",
            "ALLCAN-led",
            self.paths,
            self.inputs,
            "",
            "",
            "/application/bin/libjlinkarm.dylib",
        )
        fake_jlink = Mock()
        fake_jlink.connected.return_value = True
        fake_jlink.set_tif.return_value = True
        fake_jlink.hardware_status = SimpleNamespace(voltage=0)
        thread.jlink = fake_jlink

        success, message = thread.run_jlink_flash()

        self.assertFalse(success)
        self.assertIn("目标板未供电", message)
        fake_jlink.flash_file.assert_not_called()


class FlashToolWindowTests(unittest.TestCase):
    """Verify that long failure text remains visible and accessible."""

    @classmethod
    def setUpClass(cls):
        """Create the Qt application required by widget tests."""
        cls.application = QApplication.instance() or QApplication([])
        cls.application.setFont(QFont("Arial", 10))

    def test_long_error_uses_wrapped_dynamic_status_area(self):
        """The status area must preserve a complete multi-line esptool error."""
        window = FlashToolWindow()
        error = (
            "esptool烧录失败: Failed to connect to ESP8266: No serial data received.\n"
            "For troubleshooting steps visit the esptool documentation."
        )

        window.on_flash_complete(False, error)

        expected = f"烧录失败\n{error}"
        self.assertTrue(window.status_label.wordWrap())
        self.assertEqual(expected, window.status_label.text())
        self.assertEqual(expected, window.status_label.toolTip())
        self.assertGreater(window.top_frame.maximumHeight(), 100)
        self.assertIn(error, window.text_edit.toPlainText())
        window.close()

    def test_logo_is_used_for_window_and_brand_header(self):
        """The main window must expose the bundled logo and matching brand text."""
        window = FlashToolWindow()

        self.assertEqual(APP_NAME, window.windowTitle())
        self.assertFalse(window.windowIcon().isNull())
        self.assertFalse(window.logo_label.pixmap().isNull())
        self.assertEqual("慧仓嵌入式烧录助手 Logo", window.logo_label.accessibleName())
        self.assertEqual(APP_SUBTITLE, window.logo_label.toolTip())
        title_label = window.findChild(QLabel, "brandTitleLabel")
        subtitle_label = window.findChild(QLabel, "brandSubtitleLabel")
        self.assertEqual(APP_NAME, title_label.text())
        self.assertEqual(APP_SUBTITLE, subtitle_label.text())
        window.close()

    def test_status_selectors_use_onsen_theme_in_the_status_card(self):
        """Port and rate choices must use the Onsen-style system status card."""
        window = FlashToolWindow()

        self.assertGreaterEqual(window.minimumWidth(), 860)
        self.assertEqual(820, window.height())
        self.assertIn("#f2f2f7", window.styleSheet())
        self.assertIn("background-color: #0076ff", window.styleSheet())
        self.assertIn("QFrame#panelFrame", window.styleSheet())
        self.assertIn("QComboBox#deviceCombo", window.styleSheet())
        self.assertIn("QComboBox#portCombo", window.styleSheet())
        self.assertEqual("deviceCombo", window.device_combo.objectName())
        self.assertEqual("portCombo", window.port_combo.objectName())
        self.assertEqual("baudrateCombo", window.baudrate_combo.objectName())
        self.assertIsInstance(window.port_combo, QComboBox)
        self.assertIsInstance(window.baudrate_combo, QComboBox)
        self.assertIs(window.device_detail_frame, window.port_combo.parentWidget())
        self.assertIs(window.device_detail_frame, window.baudrate_combo.parentWidget())
        self.assertFalse(hasattr(window, "connection_frame"))
        self.assertGreaterEqual(window.port_combo.minimumWidth(), 220)
        self.assertGreaterEqual(window.port_combo.view().minimumWidth(), 340)
        self.assertEqual("consoleOutput", window.text_edit.objectName())
        self.assertIn(
            window.text_edit.font().family(),
            QFontDatabase.families(),
        )
        self.assertTrue(
            QFontDatabase.isFixedPitch(window.text_edit.font().family())
        )
        self.assertNotIn("Consolas", window.styleSheet())
        self.assertFalse(window.port_combo.isEnabled())
        self.assertFalse(window.baudrate_combo.isEnabled())

        fake_ports = [SimpleNamespace(device="/dev/ttyUSB0")]
        with patch("flash_tool.list_ports.comports", return_value=fake_ports):
            window.device_combo.setCurrentText("Wled-esp8266")
            self.assertEqual("/dev/ttyUSB0", window.port_combo.currentText())
            self.assertEqual("921600", window.baudrate_combo.currentText())
            self.assertEqual(
                ["115200", "230400", "460800", "921600"],
                [
                    window.baudrate_combo.itemText(index)
                    for index in range(window.baudrate_combo.count())
                ],
            )

        window.device_combo.setCurrentText("ALLCAN-led")
        self.assertEqual("J-Link / SWD", window.port_combo.currentText())
        self.assertEqual("4 MHz", window.baudrate_combo.currentText())
        window.close()

    def test_device_details_and_ready_indicator_follow_selection(self):
        """Device metadata and the pulse timer must reflect configuration readiness."""
        window = FlashToolWindow()

        window.device_combo.setCurrentText("ALLCAN-led")
        self.assertEqual("J-Link / SWD", window.port_combo.currentText())
        self.assertEqual("4 MHz", window.baudrate_combo.currentText())
        self.assertEqual("配置就绪", window.detail_state_value.text())
        self.assertTrue(window.ready_pulse_timer.isActive())
        self.assertTrue(window.status_label.text().startswith("✓"))

        window.device_combo.setCurrentText("请选择设备")
        self.assertFalse(window.ready_pulse_timer.isActive())
        self.assertEqual("等待选择", window.detail_state_value.text())
        window.close()

    def test_firmware_file_can_be_cleared_and_chip_uses_combo_box(self):
        """Firmware rows must provide placeholders, clear actions, and chip choices."""
        window = FlashToolWindow()
        window.device_combo.setCurrentText("ALLCAN-led")
        widgets = window.firmware_widgets["bootloader"]

        self.assertIn("点击选择文件", widgets["file_button"].text())
        self.assertFalse(widgets["file_button"].icon().isNull())
        self.assertEqual("chipCombo", widgets["device_name"].objectName())
        window.firmware_paths["bootloader"] = "/tmp/firmware.bin"
        widgets["file_button"].setText("firmware.bin")
        widgets["clear_button"].setVisible(True)

        window.clear_firmware_selection("bootloader")

        self.assertIsNone(window.firmware_paths["bootloader"])
        self.assertIn("点击选择文件", widgets["file_button"].text())
        self.assertTrue(widgets["clear_button"].isHidden())
        window.close()

    def test_log_filter_highlights_and_clears_cached_messages(self):
        """The terminal control bar must filter and clear the underlying log cache."""
        window = FlashToolWindow()
        window.append_log("INFO probe connected")
        window.append_log("ERROR target unavailable")

        window.log_filter_input.setText("ERROR")
        self.assertNotIn("probe connected", window.text_edit.toPlainText())
        self.assertIn("target unavailable", window.text_edit.toPlainText())

        window.clear_log()
        self.assertEqual([], window.log_messages)
        self.assertEqual("", window.text_edit.toPlainText())
        window.close()


class STResourceSyncTests(unittest.TestCase):
    """Verify Devices.xml version checks and atomic ST directory replacement."""

    def create_source(self, root, devices_content="new-devices"):
        """Create a representative bundled ST resource directory."""
        source = Path(root) / "source-ST"
        source.mkdir()
        (source / "Devices.xml").write_text(devices_content, encoding="utf-8")
        (source / "flash-loader.elf").write_bytes(b"new loader")
        return source

    def test_matching_devices_xml_skips_copy(self):
        """Matching Devices.xml content must leave the installed ST tree untouched."""
        with tempfile.TemporaryDirectory() as directory:
            source = self.create_source(directory)
            target = Path(directory) / "JLinkDevices/ST"
            target.mkdir(parents=True)
            (target / "Devices.xml").write_text("new-devices", encoding="utf-8")
            (target / "local-custom-file").write_text("keep", encoding="utf-8")

            updated = FlashToolWindow.synchronize_st_resources(source, target)

            self.assertFalse(updated)
            self.assertTrue((target / "local-custom-file").exists())

    def test_mismatched_devices_xml_replaces_entire_st_tree(self):
        """A version mismatch must replace all installed ST resources."""
        with tempfile.TemporaryDirectory() as directory:
            source = self.create_source(directory)
            target = Path(directory) / "JLinkDevices/ST"
            target.mkdir(parents=True)
            (target / "Devices.xml").write_text("old-devices", encoding="utf-8")
            (target / "obsolete-loader.sfl").write_bytes(b"obsolete")

            updated = FlashToolWindow.synchronize_st_resources(source, target)

            self.assertTrue(updated)
            self.assertEqual("new-devices", (target / "Devices.xml").read_text())
            self.assertEqual(b"new loader", (target / "flash-loader.elf").read_bytes())
            self.assertFalse((target / "obsolete-loader.sfl").exists())

    def test_missing_target_is_copied(self):
        """A missing target ST directory must be populated from bundled resources."""
        with tempfile.TemporaryDirectory() as directory:
            source = self.create_source(directory)
            target = Path(directory) / "JLinkDevices/ST"

            updated = FlashToolWindow.synchronize_st_resources(source, target)

            self.assertTrue(updated)
            self.assertEqual("new-devices", (target / "Devices.xml").read_text())
            self.assertTrue((target / "flash-loader.elf").is_file())

    def test_failed_replacement_restores_previous_st_tree(self):
        """A replacement failure must roll back the previous ST directory."""
        with tempfile.TemporaryDirectory() as directory:
            source = self.create_source(directory)
            target = Path(directory) / "JLinkDevices/ST"
            target.mkdir(parents=True)
            (target / "Devices.xml").write_text("old-devices", encoding="utf-8")
            (target / "old-loader.elf").write_bytes(b"old loader")
            real_replace = os.replace
            call_count = 0

            def fail_second_replace(source_path, target_path):
                """Fail after the previous target has been moved into the backup."""
                nonlocal call_count
                call_count += 1
                if call_count == 2:
                    raise OSError("simulated replacement failure")
                return real_replace(source_path, target_path)

            with patch("flash_tool.os.replace", side_effect=fail_second_replace):
                with self.assertRaisesRegex(OSError, "simulated replacement failure"):
                    FlashToolWindow.synchronize_st_resources(source, target)

            self.assertEqual("old-devices", (target / "Devices.xml").read_text())
            self.assertEqual(b"old loader", (target / "old-loader.elf").read_bytes())


if __name__ == "__main__":
    unittest.main()
