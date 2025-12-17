from typing import Type, Optional, Callable, TypeVar
from PySide6.QtCore import SignalInstance, QObject
from PySide6.QtWidgets import QWidget


type_widget = TypeVar('type_widget', bound=QWidget)  # 泛型类型约束


def find_connect_widgets(
        ui: QWidget,
        widget_type: Type[type_widget],
        widget_name: str,
        signal: SignalInstance | str,  # signal
        slot: Callable[..., None],  # slot function
        connect_args: Optional[dict] = None  # connect args
) -> Optional[type_widget]:
    """查找控件并连接信号（修正后的方法名）"""
    widget = ui.findChild(widget_type, widget_name)
    if not widget:
        print(f"错误：未找到控件 '{widget_name}'")
        return None
        # 处理信号连接
    try:
        # 自动处理带参数的信号
        target_signal = getattr(widget, signal) if isinstance(signal, str) else signal
        if connect_args:
            target_signal.connect(slot, **connect_args)
        else:
            target_signal.connect(slot)

        print(f"成功连接 {widget_name} 的 {signal if isinstance(signal, str) else signal.__name__} 信号")
    except AttributeError as e:
        print(f"信号连接失败：{str(e)}")
    except TypeError as e:
        print(f"参数不匹配：{str(e)}")
        print("提示：可使用 lambda 包装参数，例如 lambda: slot()")
    except Exception as e:
        print(f"未知连接错误：{str(e)}")
    return widget


def auto_bind_ui_members(father_ui: QWidget, child_ui: QWidget, use_meta_object: bool = False):
    """自动绑定所有UI控件到父UI的属性"""
    if not child_ui:
        return

    # 方法1: 通过对象名称自动绑定
    for child in child_ui.findChildren(QWidget):
        if child.objectName():
            if hasattr(father_ui, child.objectName()):
                print(f"警告：属性 {child.objectName()} 已存在，跳过绑定")
                continue
            setattr(father_ui, child.objectName(), child)
            print(f"{child_ui.objectName()} 已绑定")

    # 方法2: 通过元对象系统获取属性（可选）
    if use_meta_object:
        meta_object = child_ui.metaObject()
        for i in range(meta_object.propertyCount()):
            prop = meta_object.property(i)
            if prop.isValid() and prop.typeName() == "QWidget*":
                widget = prop.read(child_ui)
                if widget and widget.objectName():
                    if hasattr(father_ui, widget.objectName()):
                        print(f"警告：属性 {widget.objectName()} 已存在，跳过绑定")
                        continue
                    setattr(father_ui, widget.objectName(), widget)


def hello():
    print("Hello!")
