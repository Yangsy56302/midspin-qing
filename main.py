import shutil
import time
import tkinter as tk
from tkinter import filedialog, Menu
from typing import NotRequired, TypedDict
from PIL import Image, ImageTk, ImageDraw
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
    if 0 <= t < 0.125:
        ease_func = easing.CubicEaseOut(start=0, end=-0.5, duration=0.125) # type: ignore
        ease_val: float = ease_func.ease(t)
        return 1 - ease_val, 1 + ease_val
    elif 0.125 <= t < 1:
        ease_func = easing.ElasticEaseOut(start=-0.5, end=0, duration=1.25) # type: ignore
        ease_val: float = ease_func.ease(t - 0.125)
        return 1 - ease_val, 1 + ease_val
    else: # 边界处理
        return 1.0, 1.0


# 确保打包后能找到资源
def resource_path(relative_path: str) -> str:
    """获取资源的绝对路径（用于pyinstaller打包）"""
    base_path: str
    try:
        base_path = sys._MEIPASS # type: ignore
    except AttributeError:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)


path_config: str = "config.yml"
class Config(TypedDict):
    char: str # 当前所选自定义角色的文件夹
    fps: int # 帧率
    topmost: bool # 置顶？
    echo: bool # 音效可叠加？若是，则高速戳晴时很可能会吞音

config: Config
default_config: Config = Config({
    "char": "./miss_qing",
    "fps": 60,
    "topmost": True,
    "echo": False,
})


path_char_config: str = "config.yml"
class CharConfig(TypedDict):
    sound: str # 中旋音效文件相对路径
    image: str # 晴立绘文件相对路径
    # image_link: NotRequired[str] # 未戳过
    # image_hover: NotRequired[str] # 悬停时
    image_active: NotRequired[str] # 戳动画
    # image_visited: NotRequired[str] # 戳完后
    icon: NotRequired[str] # 托盘图标文件相对路径
    miyu_color: str # 将被视为透明的颜色

char_config: CharConfig
default_char_config: CharConfig = CharConfig({
    "sound": "sndReverbClack.wav",
    "image": "Miss Qing.png",
    "miyu_color": "#AD0FA1",
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

def dump_config(cfg: Config | None = None, /):
    if cfg is None: cfg = config
    with open(resource_path(path_config), "w") as f:
        yaml.dump(cfg, f)

def dump_char_config(cfg: CharConfig | None = None, /):
    if cfg is None: cfg = char_config
    with open(char_res_path(path_char_config), "w") as f:
        yaml.dump(cfg, f)


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
    alp = alp.point(lambda a: 0x00 if a < thr else 0xFF) # type: ignore 二值化
    img.putalpha(alp) # 覆盖透明度通道
    return img


class FloatingImage:
    def __init__(self, root: tk.Tk):
        self.root: tk.Tk = root
        self.root.overrideredirect(True)  # 无边框
        self.root.attributes('-topmost', config["topmost"])  # 最上层显示
        self.root.attributes('-transparentcolor', char_config["miyu_color"])  # 透明色（根据图片调整）

        # 初始化音效
        pygame.mixer.init()
        self.sound: pygame.mixer.Sound = pygame.mixer.Sound(char_res_path(char_config["sound"]))

        # 初始化图片
        self.load_image()

        # 拖动相关
        self.dragging: bool = False
        self.start_x: int = 0
        self.start_y: int = 0

        # 绑定事件
        self.canvas.bind("<Button-1>", self.on_click)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<Button-3>", self.show_right_menu)
        self.root.bind("<Key>", self.on_key_press)
        
        self.create_right_menu() # 创建右键菜单
        self.create_tray() # 创建系统托盘
        self.root.geometry(f"{self.width}x{self.height}+100+100") # 调整窗口大小和位置

        # 欢迎
        self.start_animation()
        
    def load_image(self):
        """加载图片并保持原始像素"""
        missing_image: Image.Image = Image.new("RGBA", (0x100, 0x100), "#F800F8")
        draw = ImageDraw.Draw(missing_image)
        draw.rectangle(((0x0, 0x0), (0x80, 0x80)), fill="#000000")
        draw.rectangle(((0x80, 0x80), (0x100, 0x100)), fill="#000000")
        draw.text((0x20, 0x10), ":(", fill="#F800F8", font_size=0x40)
        
        self.image: Image.Image
        try:
            self.image = threshold(Image.open(char_res_path(char_config["image"])).convert("RGBA"))
        except Exception:
            self.image = missing_image.copy()
        
        self.image_active: Image.Image
        image_active_path = char_config.get("image_active", None)
        if image_active_path is not None:
            try:
                self.image_active = threshold(Image.open(char_res_path(image_active_path)).convert("RGBA"))
            except Exception as e:
                print(e)
                self.image_active = missing_image.copy()
        else:
            self.image_active = self.image.copy()

        # 动画相关
        self.animating: bool = False # 动画是否正在播放
        self.current_frame: int = 0 # 动画当前位于第几帧
        self.duration: float = 1.0 # 动画总时长（秒）
        self.gen_frames()
        
        # 略微扩展画布大小，以避免图像在动画过程中溢出画布范围
        self.width: int = int(self.image.size[0] * 1.5)
        self.height: int = int(self.image.size[1] * 1.5)
        
        # 禁用高DPI缩放，保持原始像素
        self.root.tk.call('tk', 'scaling', 1.0)

        # 重新创建画布
        self.canvas: tk.Canvas = tk.Canvas(
            self.root, width=self.width, height=self.height,
            highlightthickness=0, bg=char_config["miyu_color"]
        )
        self.canvas.pack()

        # 转换为tkinter可用格式
        self.tk_image: ImageTk.PhotoImage = ImageTk.PhotoImage(self.image)

        # 底部对齐
        x: int = (self.width - self.tk_image.width()) // 2
        y: int = self.height - self.tk_image.height()

        self.canvas_image: int = self.canvas.create_image(x, y, anchor=tk.NW, image=self.tk_image)
            
    def start_animation(self):
        """开始播放动画"""
        self.animation_start_time: float = time.time()
        threading.Thread(target=self.play_sound).start()
        self.current_frame = 0
        if not self.animating:
            self.animating = True
            self.animate()

    def on_click(self, event: tk.Event):
        """左键点击事件"""
        self.start_animation()
        if self.animating:
            # 记录拖动起始位置
            self.dragging = True
            self.start_x = event.x
            self.start_y = event.y

    def on_key_press(self, event: tk.Event):
        """键盘按键事件"""
        self.start_animation()

    def on_drag(self, event: tk.Event):
        """左键拖动事件"""
        if self.dragging:
            # 计算新位置
            x: int = self.root.winfo_x() + (event.x - self.start_x)
            y: int = self.root.winfo_y() + (event.y - self.start_y)
            self.root.geometry(f"+{x}+{y}")

    def on_release(self, event: tk.Event):
        """左键释放事件"""
        self.dragging = False

    def play_sound(self) -> None:
        """播放音效"""
        try:
            if not config["echo"]:
                self.sound.stop()
            self.sound.play()
        except:
            pass
    
    def gen_frames(self) -> None:
        """提前生成所有动画帧"""
        self.animation: list[Image.Image] = [] # 动画帧列表
        for frame in range(int(self.duration * config["fps"])):
            x_factor, y_factor = custom_easing_curve(frame / (self.duration * config["fps"]))
            img: Image.Image = self.image_active.resize((
                int(self.image_active.size[0] * x_factor),
                int(self.image_active.size[1] * y_factor)
            ), Image.Resampling.BILINEAR)
            self.animation.append(threshold(img))

    def animate(self) -> None:
        """弹跳动画（纵轴缩放）"""
        if not self.animating:
            return

        # 计算当前应当播放第几帧，并判断是否播放完毕
        self.current_frame = int((time.time() - self.animation_start_time) * config["fps"])
        if self.current_frame >= self.duration * config["fps"]:
            self.animating = False
            self.tk_image = ImageTk.PhotoImage(self.image)
            self.canvas.itemconfig(self.canvas_image, image=self.tk_image)
            self.canvas.coords(self.canvas_image, (self.width - self.image.size[0]) // 2, self.height - self.image.size[1])
            return
        
        # 设置当前所显示的帧
        self.tk_image = ImageTk.PhotoImage(self.animation[self.current_frame])
        self.canvas.itemconfig(self.canvas_image, image=self.tk_image)

        # 底部对齐
        new_x: int = (self.width - self.tk_image.width()) // 2
        new_y: int = self.height - self.tk_image.height()
        self.canvas.coords(self.canvas_image, new_x, new_y)

        # 准备播放下一帧动画
        self.root.after(500 // config["fps"], self.animate)
    
    def summon(self):
        """将晴召唤至窗口顶层"""
        self.root.focus_force()
        self.start_animation()

    def change_image(self):
        """更换图片"""
        file_path: str = filedialog.askopenfilename(
            title="选择晴…",
            initialdir=char_res_path(""),
            filetypes=[("支持的图片文件", "*.png *.gif *.jpg *.jpeg *.bmp *.webp"), ("所有文件", "*")]
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
            title="选择中旋…",
            initialdir=char_res_path(""),
            filetypes=[("音频文件", "*.wav *.mp3 *.ogg *.flac"), ("所有文件", "*")]
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
            title="导入角色…",
            initialdir=resource_path(""),
        )
        if file_path:
            config["char"] = resource_path(file_path)
            dump_config()
            self.restart_app()

    def dump_char(self):
        """导出当前角色配置至文件夹"""
        file_path: str = filedialog.askdirectory(
            title="导出角色…",
            initialdir=resource_path(""),
        )
        if file_path:
            shutil.copytree(resource_path(config["char"]), resource_path(file_path), dirs_exist_ok=True)
            # self.restart_app()

    def switch_topmost(self):
        """切换窗口置顶状态"""
        config["topmost"] = not config["topmost"]
        dump_config()
        self.root.attributes('-topmost', config["topmost"])

    def create_right_menu(self) -> None:
        """创建右键菜单"""
        self.right_menu: tk.Menu = Menu(self.root, tearoff=0)
        self.right_menu.add_command(label="更换中旋…", command=self.change_sound)
        self.right_menu.add_command(label="更换晴…", command=self.change_image)
        self.right_menu.add_separator()
        self.right_menu.add_command(label="读取角色配置…", command=self.load_char)
        self.right_menu.add_command(label="克隆角色配置…", command=self.dump_char)
        self.right_menu.add_separator()
        self.right_menu.add_command(label="切换置顶", command=self.switch_topmost)
        self.right_menu.add_separator()
        self.right_menu.add_command(label="重新加载", command=self.restart_app)
        self.right_menu.add_command(label="退出", command=self.quit_app)

    def show_right_menu(self, event: tk.Event):
        """显示右键菜单"""
        try:
            self.right_menu.post(event.x_root, event.y_root)
        except:
            pass

    def create_tray(self):
        """创建系统托盘"""
        # 创建托盘图标
        tray_icon: Image.Image
        try:
            tray_icon_path = char_config.get("icon", char_config["image"])
            tray_icon = Image.open(char_res_path(tray_icon_path))
        except:
            tray_icon = self.image.copy()

        # 托盘菜单
        tray_menu: tuple[MenuItem, ...] = (
            MenuItem('召唤', self.summon, default=True),
            MenuItem('更换中旋…', self.change_sound),
            MenuItem('更换晴…', self.change_image),
            MenuItem('读取角色配置…', self.load_char),
            MenuItem('克隆角色配置…', self.dump_char),
            MenuItem('切换置顶', self.switch_topmost),
            MenuItem('重新加载', self.restart_app),
            MenuItem('退出', self.quit_app)
        )

        # 创建托盘
        self.tray = pystray.Icon("floating_image", tray_icon, "中旋晴", tray_menu)

        # 后台运行托盘
        threading.Thread(target=self.tray.run, daemon=True).start()

    def quit_app(self):
        """退出程序"""
        self.animating = False
        self.animation.clear()
        self.tray.stop()
        self.canvas.destroy()
        self.root.quit()
        self.root.destroy()
        sys.exit(0)

    def restart_app(self):
        """重启程序"""
        self.animating = False
        self.animation.clear()
        self.tray.stop()
        self.canvas.destroy()
        self.root.quit()
        self.root.destroy()
        main()


def main():
    # 加载配置
    if not os.path.exists(resource_path(path_config)): dump_config(default_config)
    load_config()
    dump_config()
    load_char_config()
    # dump_char_config()
    
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
