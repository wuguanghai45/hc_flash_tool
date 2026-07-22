"""
Device configurations for different hardware platforms.
This file contains the configuration settings for various devices including flash tools,
firmware addresses, and device names.
"""

DEVICE_CONFIGS = {
    "ALLCAN-led": {
        "flash_tool": "JLink",
        "firmware_configs": {
            "bootloader": {
                "address": "0x08000000",
                "device_name": "STM32L431RC"
            },
            "app": {
                "address": "0x0800A000",
                "device_name": "STM32L431RC"
            },
            "config": {
                "address": "0x90000000",
                "device_name": "STM32L4xx_QSPI"
            }
        }
    },
    "ALLCAN-tof": {
        "flash_tool": "JLink",
        "firmware_configs": {
            "bootloader": {
                "address": "0x08000000",
                "device_name": "STM32L431RC"
            },
            "app": {
                "address": "0x0800A000",
                "device_name": "STM32L431RC"
            }
        }
    },
    "Wled-esp8266": {
        "flash_tool": "esptool",
        "baudrate": "921600",
        "firmware_configs": {
            "app": {
                "address": "0x00000000",
                "device_name": "ESP8266"
            }
        }
    }
} 