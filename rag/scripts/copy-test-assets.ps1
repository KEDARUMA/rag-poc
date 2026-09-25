[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

# 定数: このスクリプトの場所から求めるRAGディレクトリ。
$ragRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
# 定数: 再帰コピー元の専用アセットディレクトリ。
$sourceRoot = Join-Path $ragRoot 'assets\rag-test-data'
# 定数: 再帰コピー先の入力データディレクトリ。
$destinationRoot = Join-Path $ragRoot 'data\rag-test-data'

if (-not (Test-Path -LiteralPath $sourceRoot -PathType Container)) {
    throw "コピー元ディレクトリがありません: $sourceRoot"
}

$sourcePrefix = [System.IO.Path]::GetFullPath($sourceRoot).TrimEnd('\') + '\'
$destinationPrefix = [System.IO.Path]::GetFullPath($destinationRoot).TrimEnd('\') + '\'
$files = @(Get-ChildItem -LiteralPath $sourceRoot -File -Recurse -Force)
New-Item -ItemType Directory -Path $destinationRoot -Force | Out-Null

$copiedCount = 0
foreach ($file in $files) {
    $relativePath = $file.FullName.Substring($sourcePrefix.Length)
    $targetPath = [System.IO.Path]::GetFullPath((Join-Path $destinationRoot $relativePath))
    if (-not $targetPath.StartsWith($destinationPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "コピー先がデータディレクトリの外を指しています: $targetPath"
    }

    $targetDirectory = Split-Path -Parent $targetPath
    New-Item -ItemType Directory -Path $targetDirectory -Force | Out-Null
    Copy-Item -LiteralPath $file.FullName -Destination $targetPath -Force
    $copiedCount++
}

Write-Host "アセットを $copiedCount 件コピーしました: $destinationRoot"
