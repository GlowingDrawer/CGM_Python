import sys
from typing import Type, Optional, Callable, TypeVar
from PySide6.QtWidgets import QApplication, QMainWindow, QPushButton, QWidget, QStackedWidget
from PySide6.QtUiTools import QUiLoader
from pathlib import Path
from MyQtMethods import connect

type_widget = TypeVar('type_widget', bound=QWidget)  # 泛型类型约束

page_addr = r"D:\AppDocuments\QT\PyQt\UI\UI_02_MyPage.ui"


class PagesWindow(QMainWindow):
    def __init__(self, ui_address: str):
        super().__init__()
        loader = QUiLoader()
        try:
            self.ui = loader.load(ui_address, self)
        except Exception as e:
            print(f"加载UI文件失败: {e}")
            self.ui = None

        if not self.ui:
            print("错误：UI文件加载失败")
            return
        self.stacked_widget = self.ui.findChild(QStackedWidget, "stackedWidget")
        if not self.stacked_widget:
            raise RuntimeError("未找到 stackedWidget 控件")
        self.__auto_connect_buttons()
        # 直接使用加载的 QMainWindow
        self.setWindowTitle("NetEasePage")
        self.setCentralWidget(self.ui)
        self.resize(800, 600)
        self.show()

    def __auto_connect_buttons(self):
        """自动连接所有 pushButton_ 前缀的按钮"""
        buttons = self.ui.findChildren(QPushButton)
        for btn in buttons:
            if btn.objectName().startswith("pushButton_"):
                slot_name = f"on_{btn.objectName()}_clicked"
                slot = getattr(self, slot_name, None)
                if callable(slot):
                    btn.clicked.connect(slot)
                    print(f"{btn.objectName()} 已连接")
                else:
                    print(f"警告: 未找到槽函数 {slot_name}")

    def on_pushButton_page1_clicked(self):
        print("Page1 Button Clicked")
        self.stacked_widget.setCurrentIndex(0)

    def on_pushButton_page2_clicked(self):
        print("Page2 Button Clicked")
        self.stacked_widget.setCurrentIndex(1)

    def on_pushButton_page3_clicked(self):
        print("Page3 Button Clicked")
        self.stacked_widget.setCurrentIndex(2)


if __name__ == '__main__':
    ui_file = Path(page_addr)
    if not ui_file.exists():
        print(f"Error: UI文件未找到！路径：{page_addr}")
    app = QApplication(sys.argv)
    app.setStyle("gtk")
    window = PagesWindow(ui_address=page_addr)
    window.show()
    sys.exit(app.exec())
