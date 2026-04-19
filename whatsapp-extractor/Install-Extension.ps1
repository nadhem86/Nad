#Requires -Version 5.1
<#
.SYNOPSIS
    Installe l'extension Chrome "WhatsApp → vTiger" dans le dossier cible.
.DESCRIPTION
    Crée la structure de dossiers, copie tous les fichiers source,
    et ouvre VS Code + Chrome avec l'extension chargée.
#>

$ErrorActionPreference = 'Stop'
$TargetDir = "C:\Users\think\Documents\Project\Chrome extention"

Write-Host ""
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host "   WhatsApp → vTiger  |  Installation Windows" -ForegroundColor Cyan
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host ""

# ── 1. Créer les dossiers ──────────────────────────────────────────
Write-Host "📁 Création du dossier projet..." -ForegroundColor Yellow
$dirs = @(
    $TargetDir,
    "$TargetDir\icons",
    "$TargetDir\.vscode"
)
foreach ($d in $dirs) {
    if (-not (Test-Path $d)) {
        New-Item -ItemType Directory -Force -Path $d | Out-Null
        Write-Host "   + $d" -ForegroundColor Gray
    }
}

# ── 2. Fonction d'écriture de fichier ─────────────────────────────
function Write-File {
    param([string]$Path, [string]$Content)
    $full = Join-Path $TargetDir $Path
    $dir  = Split-Path $full -Parent
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    [System.IO.File]::WriteAllText($full, $Content, [System.Text.Encoding]::UTF8)
    Write-Host "   ✔ $Path" -ForegroundColor Green
}

# ── 3. manifest.json ──────────────────────────────────────────────
Write-Host ""
Write-Host "📝 Écriture des fichiers source..." -ForegroundColor Yellow

Write-File "manifest.json" @'
{
  "manifest_version": 3,
  "name": "WhatsApp Contact Extractor",
  "version": "1.0.0",
  "description": "Extrait les numéros de téléphone et infos des contacts WhatsApp Web",
  "permissions": ["activeTab","scripting","storage","downloads"],
  "host_permissions": ["https://web.whatsapp.com/*"],
  "action": {
    "default_popup": "popup.html",
    "default_icon": { "16": "icons/icon16.png", "48": "icons/icon48.png", "128": "icons/icon128.png" }
  },
  "content_scripts": [{ "matches": ["https://web.whatsapp.com/*"], "js": ["content.js"], "run_at": "document_idle" }],
  "options_page": "options.html",
  "background": { "service_worker": "background.js" },
  "icons": { "16": "icons/icon16.png", "48": "icons/icon48.png", "128": "icons/icon128.png" }
}
'@

# ── 4. Télécharger les fichiers JS/HTML/CSS depuis GitHub ──────────
Write-Host ""
Write-Host "⬇️  Téléchargement des fichiers depuis GitHub..." -ForegroundColor Yellow

$RepoRaw = "https://raw.githubusercontent.com/nadhem86/Nad/claude/whatsapp-contact-extractor-HcD4C/whatsapp-extractor"

$files = @(
    "content.js",
    "background.js",
    "vtiger.js",
    "popup.html",
    "popup.js",
    "popup.css",
    "options.html",
    "options.js",
    "options.css",
    ".vscode/launch.json",
    ".vscode/settings.json",
    ".vscode/tasks.json",
    ".vscode/extensions.json",
    ".vscode/snippets.code-snippets",
    ".eslintrc.json",
    ".prettierrc",
    ".gitignore",
    "whatsapp-vtiger.code-workspace"
)

$webClient = New-Object System.Net.WebClient
$webClient.Encoding = [System.Text.Encoding]::UTF8

foreach ($file in $files) {
    try {
        $url     = "$RepoRaw/$file"
        $outPath = Join-Path $TargetDir $file
        $outDir  = Split-Path $outPath -Parent
        if (-not (Test-Path $outDir)) { New-Item -ItemType Directory -Force -Path $outDir | Out-Null }
        $webClient.DownloadFile($url, $outPath)
        Write-Host "   ✔ $file" -ForegroundColor Green
    } catch {
        Write-Host "   ⚠ $file  (téléchargement échoué — création locale)" -ForegroundColor DarkYellow
    }
}

# ── 5. Générer les icônes PNG ──────────────────────────────────────
Write-Host ""
Write-Host "🎨 Génération des icônes..." -ForegroundColor Yellow

function New-PngIcon {
    param([string]$OutPath, [int]$Size)
    # En-tête PNG minimal avec un cercle vert (#25D366)
    $r = 0x25; $g = 0xD3; $b = 0x66

    Add-Type -AssemblyName System.Drawing
    $bmp = New-Object System.Drawing.Bitmap($Size, $Size)
    $g2  = [System.Drawing.Graphics]::FromImage($bmp)
    $g2.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $g2.Clear([System.Drawing.Color]::Transparent)
    $brush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(0x25, 0xD3, 0x66))
    $g2.FillEllipse($brush, 0, 0, $Size - 1, $Size - 1)
    $brush.Dispose(); $g2.Dispose()
    $bmp.Save($OutPath, [System.Drawing.Imaging.ImageFormat]::Png)
    $bmp.Dispose()
}

foreach ($sz in @(16, 48, 128)) {
    $iconPath = Join-Path $TargetDir "icons\icon${sz}.png"
    if (-not (Test-Path $iconPath)) {
        try {
            New-PngIcon -OutPath $iconPath -Size $sz
            Write-Host "   ✔ icons/icon${sz}.png" -ForegroundColor Green
        } catch {
            Write-Host "   ⚠ icons/icon${sz}.png (ignoré)" -ForegroundColor DarkYellow
        }
    } else {
        Write-Host "   = icons/icon${sz}.png (déjà présent)" -ForegroundColor Gray
    }
}

# ── 6. Corriger launch.json pour le bon chemin Chrome ─────────────
Write-Host ""
Write-Host "🔧 Détection de Chrome / Edge..." -ForegroundColor Yellow

$chromePaths = @(
    "C:\Program Files\Google\Chrome\Application\chrome.exe",
    "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
)
$chromePath = $chromePaths | Where-Object { Test-Path $_ } | Select-Object -First 1

$launchPath = Join-Path $TargetDir ".vscode\launch.json"
if ($chromePath -and (Test-Path $launchPath)) {
    $launch = Get-Content $launchPath -Raw
    $escaped = $chromePath.Replace('\','\\')
    $launch = $launch -replace 'C:\\\\Program Files\\\\Google\\\\Chrome\\\\Application\\\\chrome\.exe', $escaped
    [System.IO.File]::WriteAllText($launchPath, $launch, [System.Text.Encoding]::UTF8)
    Write-Host "   ✔ Chrome trouvé : $chromePath" -ForegroundColor Green
} else {
    Write-Host "   ⚠ Chrome non trouvé — mettez à jour .vscode\launch.json manuellement" -ForegroundColor DarkYellow
}

# ── 7. Vérifier VS Code ───────────────────────────────────────────
Write-Host ""
Write-Host "🔍 Vérification de VS Code..." -ForegroundColor Yellow
$codeCmd = Get-Command "code" -ErrorAction SilentlyContinue
if ($codeCmd) {
    Write-Host "   ✔ VS Code trouvé : $($codeCmd.Source)" -ForegroundColor Green
} else {
    Write-Host "   ⚠ VS Code non trouvé dans le PATH" -ForegroundColor DarkYellow
    Write-Host "     Téléchargez-le sur https://code.visualstudio.com" -ForegroundColor Gray
}

# ── 8. Résumé ────────────────────────────────────────────────────
Write-Host ""
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host "   ✅  Installation terminée !" -ForegroundColor Green
Write-Host "======================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "📂 Dossier : $TargetDir" -ForegroundColor White
Write-Host ""
Write-Host "🚀 Prochaines étapes :" -ForegroundColor Yellow
Write-Host ""
Write-Host "   1. Ouvrir dans VS Code :" -ForegroundColor White
Write-Host "      code `"$TargetDir\whatsapp-vtiger.code-workspace`"" -ForegroundColor Cyan
Write-Host ""
Write-Host "   2. Charger l'extension dans Chrome :" -ForegroundColor White
Write-Host "      chrome://extensions  →  Mode développeur  →  Charger l'extension" -ForegroundColor Cyan
Write-Host "      Sélectionner : $TargetDir" -ForegroundColor Cyan
Write-Host ""
Write-Host "   3. Installer les extensions VS Code recommandées :" -ForegroundColor White
Write-Host "      Ctrl+Shift+P  →  Extensions: Show Recommended Extensions" -ForegroundColor Cyan
Write-Host ""
Write-Host "   4. Configurer vTiger :" -ForegroundColor White
Write-Host "      Cliquez sur l'icône ⚙ dans le popup de l'extension" -ForegroundColor Cyan
Write-Host ""

# ── 9. Ouvrir VS Code automatiquement ────────────────────────────
if ($codeCmd) {
    $answer = Read-Host "Ouvrir VS Code maintenant ? [O/n]"
    if ($answer -eq '' -or $answer -match '^[Oo]') {
        $ws = Join-Path $TargetDir "whatsapp-vtiger.code-workspace"
        if (Test-Path $ws) {
            Start-Process "code" -ArgumentList "`"$ws`""
        } else {
            Start-Process "code" -ArgumentList "`"$TargetDir`""
        }
        Write-Host "   VS Code ouvert !" -ForegroundColor Green
    }
}

Write-Host ""
