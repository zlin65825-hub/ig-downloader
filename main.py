"""IG Downloader (Kivy)
粘贴 Instagram 帖子 / Reels 链接 -> 下载原始无水印文件 -> 保存到「下载/IGDownloader」。
"""
import mimetypes
import os
import re
import shutil
import threading
import time
from pathlib import Path

# p4a 打包的 Python 没有 _lzma 模块，而 instaloader 会 import lzma：
# 这里放一个用 gzip 顶替的替身（我们不保存元数据，用不到真正的 lzma）
try:
    import lzma  # noqa: F401
except ImportError:
    import gzip
    import sys
    import types

    _m = types.ModuleType("lzma")
    _m.CHECK_NONE = 0
    _m.LZMAError = type("LZMAError", (Exception,), {})
    _m.open = lambda filename, mode="rb", **kw: gzip.open(filename, mode)
    sys.modules["lzma"] = _m

try:
    import certifi

    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())
except Exception:
    pass

from kivy.app import App
from kivy.clock import Clock
from kivy.core.clipboard import Clipboard
from kivy.core.window import Window
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.utils import platform

MEDIA_EXT = {".mp4", ".mov", ".jpg", ".jpeg", ".png", ".webp"}

ZH = dict(
    title="Instagram 无水印下载",
    hint="粘贴帖子 / Reels 链接，可多个",
    sid="（可选）sessionid，提示需要登录时填写",
    paste="粘贴", clear="清空", go="下载",
    no_url="没有找到 Instagram 链接",
    ok="已保存：", fail="失败：", done="全部完成",
)
EN = dict(
    title="Instagram Downloader",
    hint="Paste post / Reel links",
    sid="(optional) sessionid, if login is required",
    paste="Paste", clear="Clear", go="Download",
    no_url="No Instagram link found",
    ok="Saved: ", fail="Failed: ", done="All done",
)


def find_cjk_font():
    """Kivy 默认字体不含中文：到系统字体里找一个 CJK 字体。"""
    d = "/system/fonts"
    if not os.path.isdir(d):
        return None
    names = [n for n in os.listdir(d) if n.lower().endswith((".ttf", ".otf", ".ttc"))]
    for key in ("cjk", "hans", "sc-", "droidsansfallback"):
        hits = [n for n in sorted(names) if key in n.lower()]
        if hits:
            best = [n for n in hits if "regular" in n.lower()] or hits
            return os.path.join(d, best[0])
    return None


class _QuietLogger:
    """让 yt-dlp 不碰 sys.stderr（Kivy 的 stderr 会让它报错）。错误仍会通过异常抛出。"""

    def debug(self, msg):
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        pass

    def error(self, msg):
        pass


def download_video(url, out, cookies, progress):
    import yt_dlp

    def hook(d):
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if total:
                progress(f"{d['downloaded_bytes'] * 100 // total}%")

    opts = {
        "outtmpl": str(out / "%(uploader)s_%(id)s.%(ext)s"),
        # APK 里没有 ffmpeg，不能合并音视频，所以只取自带声音的单文件
        "format": "b[ext=mp4]/b",
        "restrictfilenames": True,
        "cachedir": False,
        "logger": _QuietLogger(),
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "retries": 3,
        "socket_timeout": 30,
        "progress_hooks": [hook],
    }
    if cookies:
        opts["cookiefile"] = str(cookies)
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])


def download_photo(url, out, sessionid):
    import instaloader

    m = re.search(r"instagram\.com/(?:[^/]+/)?(?:p|reel|reels|tv)/([\w-]+)", url)
    if not m:
        raise ValueError("cannot find shortcode")
    L = instaloader.Instaloader(
        dirname_pattern=str(out),
        filename_pattern="{profile}_{shortcode}",
        download_video_thumbnails=False,
        download_comments=False,
        save_metadata=False,
        post_metadata_txt_pattern="",
        compress_json=False,
        quiet=True,
    )
    if sessionid:
        try:
            L.context._session.cookies.set("sessionid", sessionid, domain=".instagram.com")
        except Exception:
            pass
    post = instaloader.Post.from_shortcode(L.context, m.group(1))
    L.download_post(post, target=out.name)


def save_to_downloads(src):
    """Android 10+：通过 MediaStore 写入公共「下载」目录，不需要存储权限。"""
    if platform != "android":
        dst = Path.home() / "Downloads" / "IGDownloader"
        dst.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst / src.name)
        return str(dst / src.name)

    from jnius import autoclass

    PythonActivity = autoclass("org.kivy.android.PythonActivity")
    ContentValues = autoclass("android.content.ContentValues")
    Downloads = autoclass("android.provider.MediaStore$Downloads")
    JFile = autoclass("java.io.File")
    Files = autoclass("java.nio.file.Files")

    resolver = PythonActivity.mActivity.getContentResolver()
    values = ContentValues()
    values.put("_display_name", src.name)
    values.put("mime_type", mimetypes.guess_type(src.name)[0] or "application/octet-stream")
    values.put("relative_path", "Download/IGDownloader")
    uri = resolver.insert(Downloads.EXTERNAL_CONTENT_URI, values)
    stream = resolver.openOutputStream(uri)
    try:
        Files.copy(JFile(str(src)).toPath(), stream)
    finally:
        stream.close()
    return "Download/IGDownloader/" + src.name


class IGApp(App):
    title = "IG Downloader"

    def build(self):
        os.environ["HOME"] = self.user_data_dir
        Window.softinput_mode = "below_target"
        self.font = find_cjk_font()
        self.T = ZH if self.font else EN
        T = self.T
        fn = {"font_name": self.font} if self.font else {}

        root = BoxLayout(orientation="vertical", padding=dp(12), spacing=dp(8))
        root.add_widget(Label(text=T["title"], font_size="20sp",
                              size_hint_y=None, height=dp(48), **fn))
        self.inp = TextInput(hint_text=T["hint"], multiline=True,
                             size_hint_y=None, height=dp(110), **fn)
        root.add_widget(self.inp)
        self.sid = TextInput(hint_text=T["sid"], multiline=False, password=True,
                             size_hint_y=None, height=dp(44), **fn)
        self.sid.text = self._load_sid()
        root.add_widget(self.sid)

        row = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
        row.add_widget(Button(text=T["paste"], on_release=self.paste, **fn))
        row.add_widget(Button(text=T["clear"], on_release=self.clear, **fn))
        self.btn = Button(text=T["go"], on_release=self.start, **fn)
        row.add_widget(self.btn)
        root.add_widget(row)

        self.status = Label(text="", size_hint_y=None, height=dp(28), **fn)
        root.add_widget(self.status)

        self.sv = ScrollView()
        self.logbox = Label(text="", size_hint_y=None, halign="left", valign="top", **fn)
        self.logbox.bind(
            width=lambda w, v: setattr(w, "text_size", (v, None)),
            texture_size=lambda w, v: setattr(w, "height", v[1]),
        )
        self.sv.add_widget(self.logbox)
        root.add_widget(self.sv)
        return root

    # ---- 小工具 ----
    def _sid_file(self):
        return Path(self.user_data_dir) / "sessionid.txt"

    def _load_sid(self):
        try:
            return self._sid_file().read_text().strip()
        except Exception:
            return ""

    def log(self, msg):
        def _do(dt):
            self.logbox.text += msg + "\n"
            self.sv.scroll_y = 0
        Clock.schedule_once(_do)

    def set_status(self, msg):
        Clock.schedule_once(lambda dt: setattr(self.status, "text", msg))

    def set_busy(self, busy):
        Clock.schedule_once(lambda dt: setattr(self.btn, "disabled", busy))

    # ---- 按钮 ----
    def paste(self, *_):
        text = Clipboard.paste() or ""
        self.inp.text = (self.inp.text + "\n" + text).strip()

    def clear(self, *_):
        self.inp.text = ""
        self.logbox.text = ""
        self.status.text = ""

    def start(self, *_):
        urls = [u for u in re.findall(r"https?://\S+", self.inp.text) if "instagram.com" in u]
        if not urls:
            self.log(self.T["no_url"])
            return
        sid = self.sid.text.strip()
        try:
            self._sid_file().write_text(sid)
        except Exception:
            pass
        self.set_busy(True)
        threading.Thread(target=self.worker, args=(urls, sid), daemon=True).start()

    # ---- 后台下载 ----
    def worker(self, urls, sid):
        T = self.T
        out = Path(self.user_data_dir) / "dl"
        out.mkdir(parents=True, exist_ok=True)
        cookies = None
        if sid:
            cookies = out.parent / "cookies.txt"
            cookies.write_text(
                "# Netscape HTTP Cookie File\n"
                ".instagram.com\tTRUE\t/\tTRUE\t2147483647\tsessionid\t" + sid + "\n"
            )
        for url in urls:
            self.log(">> " + url)
            started = time.time()
            try:
                try:
                    download_video(url, out, cookies, self.set_status)
                except Exception as e:  # 纯图片帖子会走到这里
                    self.log(str(e)[:120])
                    download_photo(url, out, sid)
                files = [
                    p for p in out.rglob("*")
                    if p.is_file() and p.suffix.lower() in MEDIA_EXT
                    and p.stat().st_mtime >= started - 1
                ]
                if not files:
                    raise RuntimeError("no media file")
                for p in files:
                    self.log(T["ok"] + save_to_downloads(p))
                    p.unlink()
            except Exception as e:
                self.log(T["fail"] + str(e)[:200])
        self.set_status("")
        self.log(T["done"])
        self.set_busy(False)


if __name__ == "__main__":
    IGApp().run()
