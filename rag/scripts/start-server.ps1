[CmdletBinding()]
param(
    [string]$ListenAddress = '127.0.0.1',
    [ValidateRange(1, 65535)]
    [int]$Port = 8765
)

$ErrorActionPreference = 'Stop'

# 定数: 専用WSLディストリビューションとプロジェクトの場所。
$DistroName = 'RAG-Ubuntu'
$RagRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path

if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
    throw 'wsl.exe が見つかりません。先にWindowsのWSL 2を利用可能にしてください。'
}

if ($RagRoot -notmatch '^[A-Za-z]:\\') {
    throw "WSLから参照できるドライブ文字のパスが必要です: $RagRoot"
}

$DriveLetter = $RagRoot.Substring(0, 1).ToLowerInvariant()
$RelativePath = $RagRoot.Substring(3).Replace('\', '/')
$WslRagRoot = "/mnt/$DriveLetter/$RelativePath"

Write-Host "RAG検索サーバーを起動します: http://localhost:$Port/"
Write-Host '停止するには Ctrl+C を押してください。'

& wsl.exe --distribution $DistroName --cd $WslRagRoot --user rag --exec `
    /home/rag/rag-runtime/.venv/bin/python ui_server.py --host $ListenAddress --port $Port

$WslExitCode = $LASTEXITCODE
if ($WslExitCode -ne 0) {
    throw "RAG検索サーバーが異常終了しました。終了コード: $WslExitCode"
}
