import sys
from PySide6.QtWidgets import QApplication, QMainWindow, QWidget, QTableWidget, QTableWidgetItem, \
    QHBoxLayout, QStackedWidget, QLabel

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("QTableWidget页面切换示例")
        self.resize(800, 400)

        # 创建主布局
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QHBoxLayout(central_widget)

        # 左侧导航表格
        self.table = QTableWidget()
        self.table.setColumnCount(1)
        self.table.setRowCount(3)
        self.table.setHorizontalHeaderLabels(["导航菜单"])
        self.table.setItem(0, 0, QTableWidgetItem("页面1"))
        self.table.setItem(1, 0, QTableWidgetItem("页面2"))
        self.table.setItem(2, 0, QTableWidgetItem("页面3"))
        self.table.clicked.connect(self.on_table_clicked)  # 绑定点击事件

        # 右侧堆叠页面
        self.stacked_widget = QStackedWidget()
        self.create_pages()  # 创建多个页面

        # 将表格和页面添加到布局
        layout.addWidget(self.table, 1)
        layout.addWidget(self.stacked_widget, 4)

    def create_pages(self):
        # 创建三个页面
        page1 = QLabel("这是页面1的内容")
        page1.setStyleSheet("background: lightblue; font-size: 20px;")
        page2 = QLabel("这是页面2的内容")
        page2.setStyleSheet("background: lightgreen; font-size: 20px;")
        page3 = QLabel("这是页面3的内容")
        page3.setStyleSheet("background: pink; font-size: 20px;")

        # 将页面添加到堆叠布局
        self.stacked_widget.addWidget(page1)
        self.stacked_widget.addWidget(page2)
        self.stacked_widget.addWidget(page3)

    def on_table_clicked(self, index):
        # 根据点击的行索引切换页面
        row = index.row()
        self.stacked_widget.setCurrentIndex(row)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())