[app]
title = IG Downloader
package.name = igdownloader
package.domain = org.example
source.dir = .
source.include_exts = py
version = 1.0
requirements = python3,kivy,pyjnius,requests,urllib3,idna,charset-normalizer,certifi,yt-dlp,instaloader
orientation = portrait
fullscreen = 0
android.permissions = INTERNET
android.api = 33
android.minapi = 26
android.archs = arm64-v8a
android.accept_sdk_license = True

[buildozer]
log_level = 2
warn_on_root = 1
