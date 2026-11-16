# 一键打包成单文件 exe（PyInstaller onefile + windowed）
#
# 用法（在仓库根目录）：
#   powershell -ExecutionPolicy Bypass -File build.ps1
#   powershell -ExecutionPolicy Bypass -File build.ps1 -Clean   # 先清空 build / dist
#
# 产物：dist\CampusNet.exe
#
# 注意：本文件刻意只用 ASCII 字符 —— Windows PowerShell 5.1 会按 ANSI 读取无 BOM
# 的 .ps1，中文注释可能导致解析失败。

param(
    [switch]$Clean
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

if ($Clean) {
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue build, dist
}

Write-Host "Building CampusNet with PyInstaller..." -ForegroundColor Cyan

$pyargs = @(
    '-m', 'PyInstaller',
    '--noconfirm',
    '--clean',
    '--onefile',
    '--windowed',
    '--name', 'CampusNet',
    '--icon', 'resources\app.ico',
    '--add-data', 'resources;resources',
    '--collect-submodules', 'src',
    '--hidden-import', 'PyQt5.QtWebEngineWidgets',
    '--hidden-import', 'PyQt5.QtWebEngineCore',
    '--hidden-import', 'PyQt5.QtWebChannel',
    '--hidden-import', 'pyqtgraph',
    '--hidden-import', 'qfluentwidgets',
    '--hidden-import', 'qframelesswindow',
    '--hidden-import', 'win32api',
    '--hidden-import', 'win32crypt',
    '--hidden-import', 'win32com.client',
    '--exclude-module', 'tkinter',
    '--exclude-module', 'matplotlib',
    '--exclude-module', 'PyQt6',
    'main.py'
)

py @pyargs
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }

Write-Host ""
Write-Host "Done: dist\CampusNet.exe" -ForegroundColor Green
