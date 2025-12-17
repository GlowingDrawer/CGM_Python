# -*- coding: utf-8 -*-
import json
import sys
import time
# import matplotlib
import serial
from PySide6.QtWidgets import QMainWindow, QApplication, QVBoxLayout, QWidget
# from PySide6.QtCore import QTimer
# import matplotlib.pyplot as plt
# from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
#
# matplotlib.use("Qt5Agg")

# 配置中文字体
# plt.rcParams['font.sans-serif'] = ['Microsoft YaHei']
# plt.rcParams['axes.unicode_minus'] = False

# 串口配置
port = 'COM4'  # 根据实际情况修改
baudrate = 115200
ser = serial.Serial(port, baudrate)


class CVPlotWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("循环伏安法(CV)曲线实时展示")
        self.setGeometry(100, 100, 800, 600)

        # 数据存储结构
        self.voltage_data = []  # 存储电压值
        self.current_data = []  # 存储电流值
        self.cycle_data = []  # 存储完整循环的数据[[V1,I1], [V2,I2], ...]
        self.cycle_count = 0  # 循环计数

        # 初始化UI
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        layout = QVBoxLayout(main_widget)

        # 创建绘图组件
        self.figure, self.ax = plt.subplots()
        self.canvas = FigureCanvas(self.figure)
        layout.addWidget(self.canvas)

        # 初始化绘图
        self.line, = self.ax.plot([], [], 'b-', lw=2)
        self.ax.set_xlabel('电压 (V)', fontsize=12)
        self.ax.set_ylabel('电流 (A)', fontsize=12)
        self.ax.set_title('循环伏安曲线', fontsize=14)
        self.ax.grid(True, linestyle='--', alpha=0.6)

        # 定时器设置
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_plot)
        self.timer.start(100)  # 每100ms更新一次

    def update_plot(self):
        if ser.in_waiting > 0:
            raw_data = ser.readline().decode().strip()
            try:
                data = json.loads(raw_data)
                voltage = float(data.get("voltage", 0))  # 从蓝牙获取电压
                current = float(data.get("current", 0))  # 从蓝牙获取电流

                # 检测电压循环（当电压第二次出现相同值时视为一个循环完成）
                if len(self.voltage_data) > 10 and abs(voltage - self.voltage_data[0]) < 0.01:
                    self.cycle_count += 1
                    self.cycle_data.append(list(zip(self.voltage_data, self.current_data)))
                    self.voltage_data, self.current_data = [], []  # 清空当前循环数据
                    print(f"完成第 {self.cycle_count} 个CV循环")

                # 添加新数据点
                self.voltage_data.append(voltage)
                self.current_data.append(current)

                # 更新绘图
                self.line.set_data(self.voltage_data, self.current_data)
                self.ax.relim()
                self.ax.autoscale_view()
                self.canvas.draw()

            except (json.JSONDecodeError, KeyError) as e:
                print(f"数据解析错误: {e}")

    def closeEvent(self, event):
        ser.close()
        # 保存完整循环数据（可选）
        with open("cv_cycles.json", "w") as f:
            json.dump(self.cycle_data, f)
        super().closeEvent(event)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = CVPlotWindow()
    window.show()
    sys.exit(app.exec())