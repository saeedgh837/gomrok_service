IRICA v1.0.14 — Visible launch feedback

What's new
----------
- Double-clicking the desktop shortcut now shows clear feedback: a Windows
  toast notification ("سرویس رجیستری فعال شد") and a clean launcher page
  in your browser with:
    * live service status + profile stats (total / running / success / blocked)
    * one-click buttons for the گمرک panel and the استعلام panel
    * a "بستن سرویس" button that shuts the whole service down cleanly
- The installer shortcut now starts the service through this new launcher.

Upgrade
-------
Double-click IRICA-Setup-v1.0.14.exe. Your data in %LOCALAPPDATA%\irica
(profiles, settings, logs) is untouched. If the panel is running, the
installer closes it first. Close any manually-started panel yourself.

SmartScreen note
----------------
The installer is not code-signed. If Windows SmartScreen appears:
click "More info" -> "Run anyway".

Checksums (SHA-256)
-------------------
9d052d0c2859de2cfc57a4c6396ec2ec291edb684b2208d8c43ea7fad266f57b  irica-v1.0.14-win64.zip
c6c040448a1030e5ab302d088954a1c8231d30107423e2103b472f6925c4491b  IRICA-Setup-v1.0.14.exe
