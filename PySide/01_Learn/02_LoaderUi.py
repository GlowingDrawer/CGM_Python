import sys
from PySide6.QtWidgets import QApplication, QMainWindow, QPushButton
from PySide6.QtUiTools import QUiLoader
from pathlib import Path

myUiAddr = r"D:\AppDocuments\QT\PyQt\CGM_01VER001\mainwindow.ui"

uiLoader = QUiLoader()


class MainWindow(QMainWindow):
    def __init__(self, ui_address: str):
        super().__init__()
        self.ui = uiLoader.load(ui_address, self)
        if not self.ui:
            print("Error: 无法加载 UI 文件！")
            return
        self.setCentralWidget(self.ui)
        self.setWindowTitle("My App")
        self.resize(800, 600)
        self.ui.show()

        # 访问控件并连接信号
        self.pushButton = self.ui.findChild(QPushButton, "pushButton_Page1")
        if self.pushButton:
            self.pushButton.clicked.connect(self.on_button_click)
            print("pushButton控件已找到")
        else:
            print("Error: 'pushButton' 控件未找到！")

    def on_button_click(self):  # 移除@staticmethod
        print("Button clicked!")

if __name__ == "__main__":
    ui_file = Path(myUiAddr)
    if not ui_file.exists():
        print(f"Error: UI文件未找到！路径：{myUiAddr}")
        sys.exit(1)

    app = QApplication(sys.argv)
    window = MainWindow(ui_address=myUiAddr)
    sys.exit(app.exec())
