import shutil
import time
import tkinter as tk
from tkinter import filedialog, Menu
from PIL import Image, ImageTk
import pygame
import threading
import pystray
from pystray import MenuItem
import sys
import os
from easing_functions import CubicEaseIn, SineEaseInOut, QuadEaseOut


def custom_easing_curve(t: float) -> tuple[float, float]:
    """
    自定义缓动曲线：1 → 0.5 → 1.1 → 1
    :param t: 归一化时间（0 ≤ t ≤ 1）
    :return: 对应数值
    """
    # 阶段1：t=0→0.3，1 → 0.5（使用缓出曲线，如QuadEaseOut）
    if 0 <= t <= 0.3:
        # 归一化当前阶段时间（0→0.3 → 0→1）
        t_norm: float = t / 0.3
        # 缓动函数：输出0→1，映射到数值区间1→0.5
        ease_func: QuadEaseOut = QuadEaseOut(start=0, end=1, duration=1)
        ease_val: float = ease_func.ease(t_norm)
        return 1 + ease_val * 0.2, 1 - ease_val * 0.5  # 1 - (0→1)*0.5 = 1→0.5

    # 阶段2：t=0.3→0.7，0.5 → 1.1（使用缓入曲线，如CubicEaseIn）
    elif 0.3 < t <= 0.7:
        # 归一化当前阶段时间（0.3→0.7 → 0→1）
        t_norm: float = (t - 0.3) / 0.4
        # 缓动函数：输出0→1，映射到数值区间0.5→1.1
        ease_func: CubicEaseIn = CubicEaseIn(start=0, end=1, duration=1)
        ease_val: float = ease_func.ease(t_norm)
        return 1.2 - ease_val * 0.3, 0.5 + ease_val * 0.6  # 0.5 + (0→1)*0.6 = 0.5→1.1

    # 阶段3：t=0.7→1.0，1.1 → 1（使用缓入缓出曲线，如SineEaseInOut）
    elif 0.7 < t <= 1.0:
        # 归一化当前阶段时间（0.7→1.0 → 0→1）
        t_norm: float = (t - 0.7) / 0.3
        # 缓动函数：输出0→1，映射到数值区间1.1→1
        ease_func: SineEaseInOut = SineEaseInOut(start=0, end=1, duration=1)
        ease_val: float = ease_func.ease(t_norm)
        return 0.9 + ease_val * 0.1, 1.1 - ease_val * 0.1  # 1.1 - (0→1)*0.1 = 1.1→1

    # 边界处理（t<0或t>1）
    elif t < 0:
        return 1.0, 1.0
    else:
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
        self.root.attributes('-transparentcolor', 'white')  # 透明色（根据图片调整）

        # 初始化音效
        pygame.mixer.init()
        self.sound: pygame.mixer.Sound = pygame.mixer.Sound(resource_path("bounce.wav"))  # 替换为你的音效文件

        # 图片相关
        self.image_path: str = image_path if image_path else resource_path("default.png")  # 默认图片
        self.load_image()

        # 拖动相关
        self.dragging: bool = False
        self.start_x: int = 0
        self.start_y: int = 0

        # 动画相关
        self.animating: bool = False
        self.animation_step: float = 0
        self.max_steps: float = 0.5  # 动画总时间（秒）
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
            self.width = int(self.original_image.size[0] * 1.2)
            self.height = int(self.original_image.size[1] * 1.1)
            # 禁用高DPI缩放，保持原始像素
            self.root.tk.call('tk', 'scaling', 1.0)

            # 重新创建画布
            if self.canvas:
                self.canvas.destroy()
            self.canvas = tk.Canvas(self.root, width=self.width, height=self.height,
                                    highlightthickness=0, bg='white')
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
                                    highlightthickness=0, bg='white')
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
        resized_image: Image.Image = self.original_image.resize((new_width, new_height), Image.Resampling.LANCZOS)
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
            self.root.after(20, self.animate)

    def create_right_menu(self):
        """创建右键菜单"""
        self.right_menu = Menu(self.root, tearoff=0)
        self.right_menu.add_command(label="更换中旋", command=self.change_sound)
        self.right_menu.add_command(label="更换晴", command=self.change_image)
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
            filetypes=[("图片文件", "*.png *.gif *.jpg *.jpeg *.bmp *.webp")]
        )
        if file_path:
            shutil.copy(file_path, resource_path("default.png"))
            self.restart_app()

    def change_sound(self):
        """更换音效"""
        file_path: str = filedialog.askopenfilename(
            title="选择中旋",
            filetypes=[("音频文件", "*.wav *.mp3 *.ogg *.flac")]
        )
        if file_path:
            shutil.copy(file_path, resource_path("bounce.wav"))
            self.restart_app()

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
    # 创建主窗口
    root: tk.Tk = tk.Tk()
    root.title("中旋晴")

    # 设置透明背景（支持透明像素）
    root.attributes('-alpha', 1.0)
    if os.name == 'nt':  # Windows系统
        root.attributes('-transparentcolor', 'white')

    # 创建悬浮图片实例
    app: FloatingImage = FloatingImage(root)

    # 运行主循环
    root.mainloop()


if __name__ == "__main__":
    # 安装依赖：pip install pillow pygame pystray
    main()
