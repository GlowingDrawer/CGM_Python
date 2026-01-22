import sys
import asyncio
from qasync import QEventLoop, asyncSlot
from bleak import BleakScanner, BleakClient, BleakError
from PySide6.QtWidgets import (QApplication, QMainWindow, QVBoxLayout, QHBoxLayout,
                               QWidget, QPushButton, QTextEdit, QComboBox, QLabel, QLineEdit, QMessageBox)
from PySide6.QtCore import Qt


class BLEController(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Python BLE 调试助手 (PySide6 + Bleak)")
        self.resize(600, 500)

        # BLE 相关的变量
        self.client = None
        self.selected_device = None
        self.devices_dict = {}  # 用于存储 {地址: 设备对象}

        # 核心 UI 组件
        self.setup_ui()

    def setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)

        # --- 顶部：扫描与选择 ---
        top_layout = QHBoxLayout()
        self.btn_scan = QPushButton("1. 扫描设备")
        self.btn_scan.clicked.connect(self.scan_devices)

        self.combo_devices = QComboBox()
        self.combo_devices.setPlaceholderText("请先扫描...")

        self.btn_connect = QPushButton("2. 连接设备")
        self.btn_connect.clicked.connect(self.toggle_connection)

        top_layout.addWidget(self.btn_scan)
        top_layout.addWidget(self.combo_devices, 1)
        top_layout.addWidget(self.btn_connect)
        layout.addLayout(top_layout)

        # --- 中部：UUID 配置 (通常连接后需要指定特征值 UUID) ---
        uuid_layout = QHBoxLayout()
        # 这里预填一个通用的 UUID，实际使用时请改为你手机 App 模拟的 UUID
        self.input_write_uuid = QLineEdit("0000ffe1-0000-1000-8000-00805f9b34fb")
        self.input_write_uuid.setPlaceholderText("写入特征值 UUID (Write)")

        self.input_notify_uuid = QLineEdit("0000ffe1-0000-1000-8000-00805f9b34fb")
        self.input_notify_uuid.setPlaceholderText("通知特征值 UUID (Notify)")

        uuid_layout.addWidget(QLabel("读写UUID:"))
        uuid_layout.addWidget(self.input_write_uuid)
        uuid_layout.addWidget(QLabel("通知UUID:"))
        uuid_layout.addWidget(self.input_notify_uuid)
        layout.addLayout(uuid_layout)

        # --- 日志显示区 ---
        self.text_log = QTextEdit()
        self.text_log.setReadOnly(True)
        layout.addWidget(self.text_log)

        # --- 底部：发送区 ---
        send_layout = QHBoxLayout()
        self.input_send = QLineEdit()
        self.input_send.setPlaceholderText("输入要发送给手机的内容...")
        self.btn_send = QPushButton("发送")
        self.btn_send.clicked.connect(self.send_data)

        send_layout.addWidget(self.input_send)
        send_layout.addWidget(self.btn_send)
        layout.addLayout(send_layout)

    def log(self, message):
        """辅助函数：在界面追加日志"""
        self.text_log.append(f">> {message}")
        # 滚动到底部
        sb = self.text_log.verticalScrollBar()
        sb.setValue(sb.maximum())

    # ------------------------------------------------------------------
    # 异步逻辑区域 (核心)
    # 使用 @asyncSlot 装饰器，让 PyQt 按钮点击事件支持 await
    # ------------------------------------------------------------------

    @asyncSlot()
    async def scan_devices(self):
        self.btn_scan.setEnabled(False)
        self.combo_devices.clear()
        self.devices_dict.clear()
        self.log("开始扫描 BLE 设备 (5秒)...")

        try:
            # 扫描 5 秒
            devices = await BleakScanner.discover(timeout=5.0)
            if not devices:
                self.log("未发现设备。请确保手机蓝牙已打开且正在广播。")

            for d in devices:
                # 过滤掉没有名字的设备（可选）
                name = d.name if d.name else "Unknown"
                display_text = f"{name} [{d.address}]"
                self.combo_devices.addItem(display_text)
                self.devices_dict[display_text] = d

            self.log(f"扫描结束，发现 {len(devices)} 个设备。")
        except Exception as e:
            self.log(f"扫描出错: {e}")
        finally:
            self.btn_scan.setEnabled(True)

    @asyncSlot()
    async def toggle_connection(self):
        # 如果已经连接，则断开
        if self.client and self.client.is_connected:
            await self.client.disconnect()
            self.client = None
            self.btn_connect.setText("2. 连接设备")
            self.btn_scan.setEnabled(True)
            self.combo_devices.setEnabled(True)
            self.log("已断开连接。")
            return

        # 进行连接
        current_text = self.combo_devices.currentText()
        if not current_text:
            QMessageBox.warning(self, "警告", "请先选择一个设备！")
            return

        device = self.devices_dict[current_text]
        self.log(f"正在连接到 {device.name}...")
        self.btn_connect.setEnabled(False)  # 防止重复点击

        try:
            self.client = BleakClient(device.address)
            await self.client.connect()

            if self.client.is_connected:
                self.log(f"连接成功！MTU: {self.client.mtu_size}")
                self.btn_connect.setText("断开连接")
                self.btn_scan.setEnabled(False)
                self.combo_devices.setEnabled(False)

                # 尝试开启通知 (Notify)
                notify_uuid = self.input_notify_uuid.text().strip()
                if notify_uuid:
                    try:
                        await self.client.start_notify(notify_uuid, self.notification_handler)
                        self.log(f"已监听特征值: {notify_uuid}")
                    except Exception as e:
                        self.log(f"监听特征值失败 (可能UUID不对): {e}")
            else:
                self.log("连接失败，未知原因。")
                self.client = None

        except Exception as e:
            self.log(f"连接发生错误: {e}")
            self.client = None
        finally:
            self.btn_connect.setEnabled(True)

    def notification_handler(self, sender, data):
        """接收手机发来的数据回调"""
        # 注意：这里的数据是 bytes 类型
        try:
            text = data.decode('utf-8')
        except:
            text = data.hex()
        self.log(f"[收] 来自手机: {text}")

    @asyncSlot()
    async def send_data(self):
        if not self.client or not self.client.is_connected:
            self.log("错误：未连接设备")
            return

        text = self.input_send.text()
        if not text:
            return

        write_uuid = self.input_write_uuid.text().strip()
        try:
            # 通常 BLE 传输需要转为 bytes
            data_bytes = text.encode('utf-8')
            # response=True 表示等待写入确认，False 表示只管发（速度快）
            await self.client.write_gatt_char(write_uuid, data_bytes, response=True)
            self.log(f"[发] {text}")
            self.input_send.clear()
        except Exception as e:
            self.log(f"发送失败: {e}")


# ------------------------------------------------------------------
# 程序入口
# ------------------------------------------------------------------
if __name__ == "__main__":
    app = QApplication(sys.argv)

    # 关键：使用 qasync 的循环替换 asyncio 的默认循环
    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)

    window = BLEController()
    window.show()

    # 启动循环
    with loop:
        loop.run_forever()