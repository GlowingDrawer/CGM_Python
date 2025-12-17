# -*- coding: utf-8 -*-
import json
import sys
import time
import serial
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QMainWindow, QApplication, QVBoxLayout, QWidget
from PySide6.QtCore import QTimer, Qt
from PySide6.QtCharts import QChart, QChartView, QLineSeries, QValueAxis

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

        # 创建Qt图表
        self.chart = QChart()
        self.chart.setTitle("循环伏安曲线")
        self.chart.legend().hide()

        # 创建坐标轴
        self.axisX = QValueAxis()
        self.axisX.setTitleText("电压 (V)")
        self.axisX.setLabelFormat("%.2f")
        self.axisX.setTickCount(10)

        self.axisY = QValueAxis()
        self.axisY.setTitleText("电流 (A)")
        self.axisY.setLabelFormat("%.2f")
        self.axisY.setTickCount(10)

        # 创建曲线系列
        self.series = QLineSeries()
        self.chart.addSeries(self.series)
        self.chart.addAxis(self.axisX, Qt.AlignBottom)
        self.chart.addAxis(self.axisY, Qt.AlignLeft)
        self.series.attachAxis(self.axisX)
        self.series.attachAxis(self.axisY)

        # 创建图表视图
        self.chart_view = QChartView(self.chart)
        self.chart_view.setRenderHint(QPainter.Antialiasing)
        layout.addWidget(self.chart_view)

        # 定时器设置
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_plot)
        self.timer.start(100)  # 每100ms更新一次

        # 初始化坐标轴范围
        self.axisX.setRange(-1, 1)
        self.axisY.setRange(-0.1, 0.1)

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
                    self.series.clear()  # 清除当前曲线
                    print(f"完成第 {self.voltage_data} 个CV循环")

                # 添加新数据点
                self.voltage_data.append(voltage)
                self.current_data.append(current)
                self.series.append(voltage, current)

                # 自动调整坐标轴范围
                if len(self.voltage_data) > 1:
                    min_x, max_x = min(self.voltage_data), max(self.voltage_data)
                    min_y, max_y = min(self.current_data), max(self.current_data)

                    # 添加一些边距
                    x_margin = (max_x - min_x) * 0.1
                    y_margin = (max_y - min_y) * 0.1

                    self.axisX.setRange(min_x - x_margin, max_x + x_margin)
                    self.axisY.setRange(min_y - y_margin, max_y + y_margin)

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