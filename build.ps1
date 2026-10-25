<#
.SYNOPSIS
  MDReader Windows 编译脚本（PyInstaller -> MDReader.exe）

  用法（在项目根目录用 PowerShell 执行）：
    powershell -ExecutionPolicy Bypass -File .\build.ps1            # 清理 -> 打包 -> 验证启动（验证完自动关进程）
    powershell -ExecutionPolicy Bypass -File .\build.ps1 -Run      # 打包后直接启动 exe（不退出）
    powershell -ExecutionPolicy Bypass -File .\build.ps1 -NoVerify # 仅打包，不验证启动

  前置条件（一次性安装依赖）：
    pip install PyQt5 PyQtWebEngine edge-tts pygame markdown pygments comtypes pyinstaller
#>
param(
    [switch]$Run,
    [switch]$NoVerify
)

# ---------------- 配置（可按需修改） ----------------
$APP_NAME   = 'MDReader'
$ENTRY      = 'main_window.py'
$CONDA_ENV  = 'md-tts-reader'   # 优先使用的 conda 环境名
$SCRIPT_DIR = $PSScriptRoot
if (-not $SCRIPT_DIR) { $SCRIPT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path }
Set-Location -Path $SCRIPT_DIR

# ------------ 定位 Python：优先 conda 环境，其次 PATH python/py ------------
$PyExe   = $null
$PyExtra = @()
# 1) 尝试激活/使用指定 conda 环境
$ca = Get-Command conda -ErrorAction SilentlyContinue
if ($ca) {
    $envPath = & conda env list 2>$null | Select-String -Pattern "^\s*$CONDA_ENV\s+(\S+)"
    if ($envPath) {
        $ep = $envPath.ToString().Trim() -split '\s+' | Select-Object -Last 1
        $cand = Join-Path $ep 'python.exe'
        if (Test-Path $cand) { $PyExe = $cand; $PyExtra = @('-E') }
    }
}
# 2) 回退到 PATH
if (-not $PyExe) {
    $g = Get-Command python -ErrorAction SilentlyContinue
    if ($g) { $PyExe = $g.Source }
    else {
        $g2 = Get-Command py -ErrorAction SilentlyContinue
        if ($g2) { $PyExe = $g2.Source; $PyExtra = @('-3') }
    }
}
if (-not $PyExe) {
    Write-Host "❌ 未找到 python / py / conda 环境 $CONDA_ENV，请先安装 Python 3.10+ 并加入 PATH" -ForegroundColor Red
    exit 1
}
Write-Host "使用 Python: $PyExe$([string]::Join(' ', $PyExtra))"

# conda 环境的 Library\bin（openssl/lzma/bz2/ffi/expat 等 Python 依赖 DLL 所在；
# PyInstaller 默认不收集这些，缺失会导致 QtWebEngineCore/ctypes 导入崩溃）
$PyBase = Split-Path -Parent $PyExe
$CondaLibBin = Join-Path $PyBase 'Library\bin'
if (-not (Test-Path $CondaLibBin)) { $CondaLibBin = $null }

# PyInstaller 缓存目录重定向到项目内，避免用户目录权限/路径问题
$env:PYINSTALLER_CONFIG_DIR = Join-Path $SCRIPT_DIR '.pyinstaller-cache'

# ---------------- 前置检查 ----------------
Write-Host "==> 检查入口文件与依赖..."
if (-not (Test-Path $ENTRY)) {
    Write-Host "❌ 入口文件 $ENTRY 不存在" -ForegroundColor Red
    exit 1
}
$deps = @('PyQt5','PyQt5.QtWebEngineWidgets','edge_tts','pygame','markdown','pygments','comtypes','PyInstaller')
$missing = @()
foreach ($d in $deps) {
    & $PyExe @PyExtra -c "import $d" 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) { $missing += $d }
}
if ($missing.Count -gt 0) {
    Write-Host "❌ 依赖缺失: $($missing -join ', ')" -ForegroundColor Red
    Write-Host "    请先安装: pip install PyQt5 PyQtWebEngine edge-tts pygame markdown pygments comtypes pyinstaller" -ForegroundColor Yellow
    exit 1
}
Write-Host "✓ 依赖检查通过"

# ---------------- 清理旧产物 ----------------
Write-Host "==> 清理旧构建产物..."
foreach ($c in @('build','dist',"$APP_NAME.spec",'.pyinstaller-cache')) {
    $p = Join-Path $SCRIPT_DIR $c
    if (Test-Path $p) { Remove-Item -Recurse -Force $p }
}

# ---------------- 打包 ----------------
# 调试时可把 --windowed 换成 --console，以观察 PyInstaller 运行期输出与异常堆栈。
Write-Host "==> 开始打包 exe（可能需要 1-2 分钟）..."
# conda 的 Python 依赖 DLL（openssl/lzma/bz2/ffi/expat）PyInstaller 默认不收集，
# 缺失会导致 QtWebEngineCore/ctypes 导入失败、启动即崩。用 --add-binary 显式注入
# 到产物根（_internal），这样每次构建都可复现，无需事后手动复制。
$AddBin = @()
$binArgs = @()
if ($CondaLibBin) {
    $extraDlls = @('libcrypto-3-x64.dll','libssl-3-x64.dll','liblzma.dll',
                   'LIBBZ2.dll','ffi.dll','libexpat.dll')
    foreach ($dll in $extraDlls) {
        $src = Join-Path $CondaLibBin $dll
        if (Test-Path $src) {
            $srcFull = (Get-Item $src).FullName
            $binArgs += '--add-binary'
            $binArgs += "$srcFull;."
            $AddBin += $dll
        }
    }
}
& $PyExe @PyExtra -m PyInstaller `
    --windowed `
    --noconfirm `
    --name $APP_NAME `
    --hidden-import edge_tts `
    --hidden-import pygame `
    --hidden-import markdown `
    --hidden-import pygments `
    --hidden-import comtypes `
    --collect-submodules edge_tts `
    --collect-submodules comtypes `
    --add-data "assets;assets" `
    @binArgs `
    $ENTRY
if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ PyInstaller 执行失败（退出码 $LASTEXITCODE）" -ForegroundColor Red
    exit 1
}

# PyInstaller 默认 onedir：exe 在 dist\<APP_NAME>\<APP_NAME>.exe，_internal 为同级目录
$EXE_PATH = Join-Path $SCRIPT_DIR "dist\$APP_NAME\$APP_NAME.exe"
if (-not (Test-Path $EXE_PATH)) {
    Write-Host "❌ 打包失败，未生成 $EXE_PATH" -ForegroundColor Red
    exit 1
}

# 校验 --add-binary 注入的 DLL 确实落在产物 _internal 根
$internalDir = Join-Path $SCRIPT_DIR "dist\$APP_NAME\_internal"
if ($AddBin.Count -gt 0) {
    Write-Host "==> 校验注入的 DLL..."
    $missingDll = @()
    foreach ($dll in $AddBin) {
        if (Test-Path (Join-Path $internalDir $dll)) {
            Write-Host "  ✓ $dll"
        } else {
            $missingDll += $dll
        }
    }
    if ($missingDll.Count -gt 0) {
        Write-Host ("  ⚠️ 以下 DLL 未注入产物: {0}（应用启动时可能崩溃）" -f ($missingDll -join ', ')) -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "✅ 打包完成！" -ForegroundColor Green
$sizeMB = (Get-Item $EXE_PATH).Length / 1MB
Write-Host ("   产物: {0}" -f $EXE_PATH)
Write-Host ("   大小: {0:N1} MB" -f $sizeMB)

# ---------------- 验证启动 ----------------
if (-not $NoVerify) {
    Write-Host ""
    Write-Host "==> 验证 exe 能否正常启动..."
    $proc = Start-Process -FilePath $EXE_PATH -PassThru
    Start-Sleep -Seconds 5
    $alive = $null
    try { $alive = Get-Process -Id $proc.Id -ErrorAction Stop } catch { }
    if ($alive) {
        Write-Host "✓ 启动成功，进程运行中 (PID=$($proc.Id))" -ForegroundColor Green
        if (-not $Run) {
            try { Stop-Process -Id $proc.Id -Force -ErrorAction Stop } catch { }
            Write-Host "  （已自动关闭验证进程；如需保持运行，用 -Run）"
        }
    } else {
        Write-Host "⚠️  未检测到进程，请双击 $EXE_PATH 查看是否报错" -ForegroundColor Yellow
        Write-Host "    日志文件: %LOCALAPPDATA%\MDReader\app.log"
    }
}

Write-Host ""
Write-Host "==> 使用方式"
Write-Host "   • 直接双击: $EXE_PATH"
Write-Host "   • 日志位置: %LOCALAPPDATA%\MDReader\app.log"
Write-Host "   • TTS 缓存: %LOCALAPPDATA%\MDReader\tts\"
Write-Host ""
Write-Host "完成。"
