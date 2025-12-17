import shutil
import time
import tkinter as tk
from tkinter import filedialog, Menu
from typing import TypedDict
from PIL import Image, ImageTk
import pygame
import threading
import pystray
from pystray import MenuItem
import sys
import os
import easing_functions as easing
import yaml
from dataclasses import dataclass


def custom_easing_curve(t: float) -> tuple[float, float]:
    """
    自定义缓动曲线
    :param t: 归一化时间（0 ≤ t ≤ 1）
    :return: 对应数值
    """
    if 0 <= t < 1/12:
        ease_func: easing.EasingBase = easing.CubicEaseOut(start=0, end=-1/2, duration=1/12 - 0)
        ease_val: float = ease_func.ease(t)
        return 1 - ease_val/2, 1 + ease_val
    elif 1/12 <= t < 1:
        ease_func: easing.EasingBase = easing.ElasticEaseOut(start=-1/2, end=0, duration=1 - 1/12)
        ease_val: float = ease_func.ease(t - 1/12)
        return 1 - ease_val/2, 1 + ease_val
    else: # 边界处理
        return 1.0, 1.0


# 确保打包后能找到资源
def resource_path(relative_path: str) -> str:
    """获取资源的绝对路径（用于pyinstaller打包）"""
    base_path: str
    try:
        base_path = sys._MEIPASS
    except AttributeError:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)


path_config: str = "config.yml"
class Config(TypedDict):
    char: str # 自定义角色文件夹

config: Config
default_config: Config = Config({
    "char": "miss_qing",
})


path_char_config: str = "config.yml"
class CharConfig(TypedDict):
    sound: str # 中旋音效文件相对路径
    image: str # 晴立绘文件相对路径
    miyu_color: str # 将被视为透明的颜色

char_config: CharConfig
default_char_config: CharConfig = CharConfig({
    "sound": "sndReverbClack.wav",
    "image": "Miss Qing.png",
    "miyu_color": "#FFFFFF",
})


def load_config():
    global config
    with open(resource_path(path_config), "r") as f:
        config = default_config.copy()
        config.update(yaml.load(f, Loader=yaml.FullLoader))

def load_char_config():
    global char_config
    with open(char_res_path(path_char_config), "r") as f:
        char_config = default_char_config.copy()
        char_config.update(yaml.load(f, Loader=yaml.FullLoader))

def dump_config():
    global config
    with open(resource_path(path_config), "w") as f:
        yaml.dump(config, f)

def dump_char_config():
    global char_config
    with open(char_res_path(path_char_config), "w") as f:
        yaml.dump(char_config, f)


# 计算角色素材路径
def char_path(relative_path: str, path: str | None = None) -> str:
    return os.path.join(config["char"] if path is None else path, relative_path)

def char_res_path(relative_path: str, path: str | None = None) -> str:
    return resource_path(char_path(relative_path, path))


def threshold(img: Image.Image, thr: float = 0xFF, /) -> Image.Image:
    """
    将图像的透明度二值化（完全透明 或 完全不透明）
    :param img: 图像
    :param thr: 阈值（不透明度小于阈值 -> 完全透明）
    :return: 图像
    """
    img = img.copy()
    alp = img.getchannel("A") # 提取透明度通道
    alp = alp.point(lambda a: 0x00 if a < thr else 0xFF) # 二值化
    img.putalpha(alp) # 覆盖透明度通道
    return img


class FloatingImage:
    def __init__(self, root: tk.Tk, image_path: str | None = None):
        self.animation_start_time: int | None = None
        self.tray: pystray.Icon = None
        self.right_menu: tk.Menu | None = None
        self.canvas: tk.Canvas | None = None
        self.width: int | None = None
        self.height: int | None = None
        self.canvas_image: int | None = None
        self.tk_image: ImageTk.PhotoImage | None = None
        self.original_image: Image.Image | None = None
        self.root: tk.Tk = root
        self.root.overrideredirect(True)  # 无边框
        self.root.attributes('-topmost', True)  # 最上层显示
        self.root.attributes('-transparentcolor', char_config["miyu_color"])  # 透明色（根据图片调整）

        # 初始化音效
        pygame.mixer.init()
        self.sound: pygame.mixer.Sound = pygame.mixer.Sound(char_res_path(char_config["sound"]))  # 替换为你的音效文件

        # 图片相关
        self.image_path: str = image_path if image_path else char_res_path(char_config["image"])  # 默认图片
        self.load_image()

        # 拖动相关
        self.dragging: bool = False
        self.start_x: int = 0
        self.start_y: int = 0

        # 动画相关
        self.animating: bool = False
        self.animation_step: float = 0
        self.max_steps: float = 1.5  # 动画总时间（秒）
        self.x_scale_factor: float = 1.0
        self.y_scale_factor: float = 1.0

        # 绑定事件
        self.canvas.bind("<Button-1>", self.on_click)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<Button-3>", self.show_right_menu)

        # 创建右键菜单
        self.create_right_menu()

        # 创建系统托盘
        self.create_tray()

        # 调整窗口大小和位置
        self.root.geometry(f"{self.width}x{self.height}+100+100")

    def load_image(self):
        """加载图片并保持原始像素"""
        try:
            self.original_image = Image.open(self.image_path).convert("RGBA")
            
            # 二值化透明度，去除白边
            self.original_image = threshold(self.original_image)
            
            # 略微扩展画布大小，以避免图像在动画过程中溢出画布范围
            self.width = int(self.original_image.size[0] * 1.5)
            self.height = int(self.original_image.size[1] * 1.5)
            
            # 禁用高DPI缩放，保持原始像素
            self.root.tk.call('tk', 'scaling', 1.0)

            # 重新创建画布
            if self.canvas:
                self.canvas.destroy()
            self.canvas = tk.Canvas(self.root, width=self.width, height=self.height,
                                    highlightthickness=0, bg=char_config["miyu_color"])
            self.canvas.pack()

            # 转换为tkinter可用格式
            self.tk_image = ImageTk.PhotoImage(self.original_image)

            # 底部对齐
            x: int = (self.width - self.tk_image.width()) // 2
            y: int = self.height - self.tk_image.height()

            self.canvas_image = self.canvas.create_image(x, y, anchor=tk.NW, image=self.tk_image)
        except Exception as e:
            print(f"加载图片失败: {e}")
            self.width, self.height = 200, 200
            self.canvas = tk.Canvas(self.root, width=self.width, height=self.height,
                                    highlightthickness=0, bg=char_config["miyu_color"])
            self.canvas.pack()
            self.canvas.create_text(100, 100, text="图片加载失败", fill="black")

    def on_click(self, event: tk.Event):
        """左键点击事件：开始拖动或播放动画"""
        # 播放弹跳动画
        self.animation_step = 0
        self.animation_start_time = time.time()
        # 播放音效
        threading.Thread(target=self.play_sound).start()
        if self.animating:
            # 记录拖动起始位置
            self.dragging = True
            self.start_x = event.x
            self.start_y = event.y
        else:
            self.animating = True
            self.animate()

    def on_drag(self, event: tk.Event):
        """拖动事件"""
        if self.dragging:
            # 计算新位置
            x: int = self.root.winfo_x() + (event.x - self.start_x)
            y: int = self.root.winfo_y() + (event.y - self.start_y)
            self.root.geometry(f"+{x}+{y}")

    def on_release(self, event: tk.Event):
        """释放左键"""
        self.dragging = False

    def play_sound(self):
        """播放音效"""
        try:
            self.sound.play()
        except:
            pass

    def animate(self):
        """弹跳动画（纵轴缩放）"""
        if not self.animating:
            return

        # 计算缩放因子（正弦曲线模拟弹跳）
        progress: float = self.animation_step / self.max_steps
        self.x_scale_factor, self.y_scale_factor = custom_easing_curve(progress)

        # 调整图片大小
        new_width: int = int(self.original_image.size[0] * self.x_scale_factor)
        new_height: int = int(self.original_image.size[1] * self.y_scale_factor)
        resized_image: Image.Image = self.original_image.resize((new_width, new_height), Image.Resampling.BILINEAR)
        resized_image = threshold(resized_image)
        self.tk_image = ImageTk.PhotoImage(resized_image)
        self.canvas.itemconfig(self.canvas_image, image=self.tk_image)

        # 底部对齐
        new_x: int = (self.width - new_width) // 2
        new_y: int = self.height - new_height
        self.canvas.coords(self.canvas_image, new_x, new_y)

        # 继续动画
        self.animation_step = time.time() - self.animation_start_time
        if self.animation_step > self.max_steps:
            self.animating = False
            self.tk_image = ImageTk.PhotoImage(self.original_image)
            self.canvas.itemconfig(self.canvas_image, image=self.tk_image)
            self.canvas.coords(self.canvas_image, (self.width - self.original_image.size[0]) // 2, self.height - self.original_image.size[1])
        else:
            self.root.after(1000 // 60, self.animate)

    def create_right_menu(self):
        """创建右键菜单"""
        self.right_menu = Menu(self.root, tearoff=0)
        self.right_menu.add_command(label="更换中旋", command=self.change_sound)
        self.right_menu.add_command(label="更换晴", command=self.change_image)
        self.right_menu.add_command(label="导入", command=self.load_char)
        self.right_menu.add_command(label="导出", command=self.dump_char)
        self.right_menu.add_separator()
        self.right_menu.add_command(label="关闭", command=self.quit_app)

    def show_right_menu(self, event: tk.Event):
        """显示右键菜单"""
        try:
            self.right_menu.post(event.x_root, event.y_root)
        except:
            pass

    def change_image(self):
        """更换图片"""
        file_path: str = filedialog.askopenfilename(
            title="选择晴",
            initialdir=char_res_path(""),
            filetypes=[("图片文件", "*.png *.gif *.jpg *.jpeg *.bmp *.webp")]
        )
        if file_path:
            try:
                shutil.copy(file_path, char_res_path(os.path.basename(file_path)))
            except shutil.SameFileError:
                pass
            char_config["image"] = os.path.basename(file_path)
            dump_char_config()
            self.restart_app()

    def change_sound(self):
        """更换音效"""
        file_path: str = filedialog.askopenfilename(
            title="选择中旋",
            initialdir=char_res_path(""),
            filetypes=[("音频文件", "*.wav *.mp3 *.ogg *.flac")]
        )
        if file_path:
            try:
                shutil.copy(file_path, char_res_path(os.path.basename(file_path)))
            except shutil.SameFileError:
                pass
            char_config["sound"] = os.path.basename(file_path)
            dump_char_config()
            self.restart_app()

    def load_char(self):
        """从文件夹导入当前角色配置"""
        file_path: str = filedialog.askdirectory(
            title="从文件夹导入",
            initialdir=resource_path(""),
        )
        if file_path:
            config["char"] = resource_path(file_path)
            dump_config()
            self.restart_app()

    def dump_char(self):
        """导出当前角色配置至文件夹"""
        file_path: str = filedialog.askdirectory(
            title="导出到文件夹",
            initialdir=resource_path(""),
        )
        if file_path:
            shutil.copytree(resource_path(config["char"]), resource_path(file_path), dirs_exist_ok=True)
            # self.restart_app()

    def create_tray(self):
        """创建系统托盘"""
        # 创建托盘图标（使用默认图片）
        tray_icon: Image.Image
        try:
            tray_icon = Image.open(resource_path("tray_icon.png")) if os.path.exists(resource_path("tray_icon.png")) else self.original_image
        except:
            tray_icon = Image.new('RGB', (64, 64), color='gray')

        # 托盘菜单
        tray_menu: tuple[MenuItem, ...] = (
            MenuItem('更换中旋', self.change_sound),
            MenuItem('更换晴', self.change_image),
            MenuItem('导入', self.load_char),
            MenuItem('导出', self.dump_char),
            MenuItem('退出', self.quit_app)
        )

        # 创建托盘
        self.tray = pystray.Icon("floating_image", tray_icon, "中旋晴", tray_menu)

        # 后台运行托盘
        threading.Thread(target=self.tray.run, daemon=True).start()

    def quit_app(self):
        """退出程序"""
        self.animating = False
        self.tray.stop()
        self.root.quit()
        self.root.destroy()
        sys.exit(0)

    def restart_app(self):
        """重启程序"""
        self.animating = False
        self.tray.stop()
        self.root.quit()
        self.root.destroy()
        main()


def main():
    # 加载配置
    load_config()
    load_char_config()
    
    # 创建主窗口
    root: tk.Tk = tk.Tk()
    root.title("中旋晴")

    # 设置透明背景（支持透明像素）
    root.attributes('-alpha', 1.0)
    if os.name == 'nt':  # Windows系统
        root.attributes('-transparentcolor', char_config["miyu_color"])

    # 创建悬浮图片实例
    app: FloatingImage = FloatingImage(root)

    # 运行主循环
    root.mainloop()


if __name__ == "__main__":
    main()
