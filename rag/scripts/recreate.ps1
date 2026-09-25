[CmdletBinding()]
param(
    [switch]$RunSmokeTest
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

# 定数: このPoCで固定したWSLディストリビューションとUbuntuイメージ。
$DistroName = 'RAG-Ubuntu'
$UbuntuUrl = 'https://releases.ubuntu.com/noble/ubuntu-24.04.5-wsl-amd64.wsl'
$UbuntuSha256 = 'bb415d824822c4b878125729af451a5d18fb13d1cf5cbed9a7393ad64ac6039e'

$RagRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$BootstrapRoot = Join-Path $RagRoot '.bootstrap'
$ArchivePath = Join-Path $BootstrapRoot 'ubuntu-24.04.5-wsl-amd64.wsl'
$PartialArchivePath = Join-Path $BootstrapRoot "ubuntu-24.04.5-wsl-amd64.$([guid]::NewGuid().ToString('N')).download"
$DistroBasePath = Join-Path $RagRoot '.wsl\RAG-Ubuntu'
$RegistryRoot = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss'

function Get-RegisteredDistro {
    <# 指定名のWSL登録情報を返す。 #>
    param([Parameter(Mandatory)][string]$Name)

    if (-not (Test-Path -LiteralPath $RegistryRoot)) {
        return $null
    }

    $Registered = Get-ChildItem -LiteralPath $RegistryRoot |
        ForEach-Object { Get-ItemProperty -LiteralPath $_.PSPath } |
        Where-Object { $_.DistributionName -eq $Name } |
        Select-Object -First 1
    return $Registered
}

function Assert-LastExitCode {
    <# 外部コマンドの失敗をその場で停止する。 #>
    param([Parameter(Mandatory)][string]$Action)

    if ($LASTEXITCODE -ne 0) {
        throw "$Action に失敗しました。終了コード: $LASTEXITCODE"
    }
}

if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
    throw 'wsl.exe が見つかりません。先にWindowsのWSL 2を利用可能にしてください。'
}

if ($RagRoot -notmatch '^[A-Za-z]:\\') {
    throw "WSLから参照できるドライブ文字のパスが必要です: $RagRoot"
}

& wsl.exe --version | Out-Null
Assert-LastExitCode 'WSLの利用確認'

New-Item -ItemType Directory -Path $BootstrapRoot -Force | Out-Null
$RegisteredDistro = Get-RegisteredDistro -Name $DistroName

if ($RegisteredDistro) {
    $ActualBasePath = ([string]$RegisteredDistro.BasePath).TrimEnd('\')
    $ActualBasePath = [System.Text.RegularExpressions.Regex]::Replace($ActualBasePath, '^\\\\\?\\', '')
    $ExpectedBasePath = [System.IO.Path]::GetFullPath($DistroBasePath).TrimEnd('\')
    if (-not [string]::Equals($ActualBasePath, $ExpectedBasePath, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "RAG-Ubuntuの登録先が異なります。現在: $ActualBasePath / 必須: $ExpectedBasePath。自動移動・削除は行いません。"
    }
    if ([int]$RegisteredDistro.Version -ne 2) {
        & wsl.exe --set-version $DistroName 2
        Assert-LastExitCode 'WSL 2への変換'
    }
    Write-Host '既存のRAG-Ubuntuを再利用します。'
} else {
    if (Test-Path -LiteralPath $ArchivePath) {
        $ActualSha256 = (Get-FileHash -LiteralPath $ArchivePath -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($ActualSha256 -ne $UbuntuSha256) {
            throw "UbuntuイメージのSHA256が一致しません: $ArchivePath"
        }
    } else {
        Write-Host 'Ubuntu 24.04.5 WSLイメージを取得しています。'
        try {
            Invoke-WebRequest -Uri $UbuntuUrl -OutFile $PartialArchivePath -UseBasicParsing
            $ActualSha256 = (Get-FileHash -LiteralPath $PartialArchivePath -Algorithm SHA256).Hash.ToLowerInvariant()
            if ($ActualSha256 -ne $UbuntuSha256) {
                throw "ダウンロードしたUbuntuイメージのSHA256が一致しません: $ActualSha256"
            }
            Move-Item -LiteralPath $PartialArchivePath -Destination $ArchivePath
        } catch {
            if (Test-Path -LiteralPath $PartialArchivePath) {
                Remove-Item -LiteralPath $PartialArchivePath -Force
            }
            throw
        }
    }

    if (Test-Path -LiteralPath $DistroBasePath) {
        $ExistingContents = Get-ChildItem -LiteralPath $DistroBasePath -Force
        if ($ExistingContents.Count -gt 0) {
            throw "未登録ですが保存先が空ではありません。内容を確認してください: $DistroBasePath"
        }
    } else {
        New-Item -ItemType Directory -Path $DistroBasePath -Force | Out-Null
    }

    Write-Host 'RAG-Ubuntuをプロジェクト内へ登録しています。'
    & wsl.exe --install --from-file $ArchivePath --name $DistroName --location $DistroBasePath --no-launch --version 2
    Assert-LastExitCode 'RAG-Ubuntuの登録'
}

$DriveLetter = $RagRoot.Substring(0, 1).ToLowerInvariant()
$RelativePath = $RagRoot.Substring(3).Replace('\', '/')
$WslRagRoot = "/mnt/$DriveLetter/$RelativePath"
$WslSetupScript = "$WslRagRoot/scripts/setup-wsl.sh"

Write-Host 'WSL内のPython、FFmpeg、CUDA依存関係を整えています。'
& wsl.exe --distribution $DistroName --user root --exec /bin/bash $WslSetupScript
Assert-LastExitCode 'WSL内のRAG環境構築'

& wsl.exe --manage $DistroName --set-default-user rag | Out-Null
Assert-LastExitCode '既定WSLユーザーの設定'

if ($RunSmokeTest) {
    Write-Host '一時データでTXT・画像・動画の登録と検索を確認しています。'
    & wsl.exe --distribution $DistroName --cd $WslRagRoot --user rag --exec /home/rag/rag-runtime/.venv/bin/python scripts/smoke-test.py
    Assert-LastExitCode 'Phase 1の動作確認'
}

Write-Host '完了しました。RAG関連ファイルは rag\ の中に配置されています。'
