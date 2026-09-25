[CmdletBinding()]
param(
    # 定数: 取得するApache Tikaのリリース版。
    [ValidatePattern('^[0-9]+\.[0-9]+\.[0-9]+$')]
    [string] $TikaTag = '4.0.0'
)

$ErrorActionPreference = 'Stop'

# 定数: スクリプトの配置場所から求めるRAGディレクトリ。
$ragRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
# 定数: ダウンロードと展開に使うrag配下の一時ディレクトリ。
$bootstrapRoot = Join-Path $ragRoot '.bootstrap'
$workRoot = Join-Path $bootstrapRoot ("apache-tika-$TikaTag-" + [guid]::NewGuid().ToString('N'))
$archivePath = Join-Path $workRoot "tika-$TikaTag-src.zip"
$extractRoot = Join-Path $workRoot 'expanded'
# 定数: バージョンごとのテストデータ保存先。
$destination = Join-Path $ragRoot "data\apache-tika-test-files\$TikaTag"

New-Item -ItemType Directory -Path $bootstrapRoot -Force | Out-Null
New-Item -ItemType Directory -Path $workRoot | Out-Null

try {
    $archiveUri = "https://downloads.apache.org/tika/$TikaTag/tika-$TikaTag-src.zip"
    $checksumUri = "$archiveUri.sha512"
    Invoke-WebRequest -Uri $archiveUri -OutFile $archivePath -UseBasicParsing

    # 定数: Apache公式チェックサムと照合するSHA-512値。
    $checksumText = (Invoke-WebRequest -Uri $checksumUri -UseBasicParsing).Content.Trim()
    $expectedHash = ($checksumText -split '\s+')[0].ToLowerInvariant()
    if ($expectedHash -notmatch '^[0-9a-f]{128}$') {
        throw 'Apache公式SHA-512チェックサムの形式が不正です。'
    }

    $actualHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA512).Hash.ToLowerInvariant()
    if ($actualHash -ne $expectedHash) {
        throw 'Apache TikaソースアーカイブのSHA-512照合に失敗しました。'
    }

    Expand-Archive -LiteralPath $archivePath -DestinationPath $extractRoot
    $sourceRoot = Get-ChildItem -LiteralPath $extractRoot -Directory |
        Where-Object {
            Test-Path -LiteralPath (Join-Path $_.FullName 'release-tools\uat\test-files') -PathType Container
        } |
        Select-Object -First 1
    if (-not $sourceRoot) {
        throw "Apache Tika $TikaTag のソースアーカイブ構成を確認できません。"
    }

    # 定数: 公式UAT手順で必要とされる4ファイル。
    $requiredUatFiles = @(
        'testPDF.pdf',
        'testHTML.html',
        'testOCR_spacing.png',
        'test_recursive_embedded.docx'
    )
    $uatDirectory = Join-Path $sourceRoot.FullName 'release-tools\uat\test-files'
    $missingFiles = @(
        foreach ($fileName in $requiredUatFiles) {
            if (-not (Test-Path -LiteralPath (Join-Path $uatDirectory $fileName) -PathType Leaf)) {
                $fileName
            }
        }
    )
    if ($missingFiles.Count -gt 0) {
        throw "公式UATテストファイルが不足しています: $($missingFiles -join ', ')"
    }

    # 定数: 全モジュールのtest-documentsとUAT用test-files。
    $fixtureDirectories = @(
        Get-ChildItem -LiteralPath $sourceRoot.FullName -Directory -Filter 'test-documents' -Recurse
    )
    $fixtureDirectories += Get-Item -LiteralPath $uatDirectory
    if ($fixtureDirectories.Count -eq 0) {
        throw 'Apache Tikaのテストデータフォルダーが見つかりません。'
    }

    $sourceRootPrefix = $sourceRoot.FullName.TrimEnd('\') + '\'
    $destinationPrefix = [System.IO.Path]::GetFullPath($destination).TrimEnd('\') + '\'
    $copiedFiles = 0
    foreach ($fixtureDirectory in $fixtureDirectories) {
        Get-ChildItem -LiteralPath $fixtureDirectory.FullName -File -Recurse -Force | ForEach-Object {
            $relativePath = $_.FullName.Substring($sourceRootPrefix.Length)
            $targetPath = [System.IO.Path]::GetFullPath((Join-Path $destination $relativePath))
            if (-not $targetPath.StartsWith($destinationPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
                throw "展開先がデータフォルダーの外を指しています: $targetPath"
            }

            $targetDirectory = Split-Path -Parent $targetPath
            New-Item -ItemType Directory -Path $targetDirectory -Force | Out-Null
            Copy-Item -LiteralPath $_.FullName -Destination $targetPath -Force
            $copiedFiles++
        }
    }

    Write-Host "Apache Tika $TikaTag のテストファイル $copiedFiles 件を展開しました: $destination"
    Write-Host '対象はソース内の全test-documentsフォルダーと公式UAT用test-filesです。'
    Write-Host 'ファイルの展開のみ行い、RAGインデックスへの登録は行いません。'
}
finally {
    # 定数: 一時領域をrag/.bootstrap配下に限定してから削除するための絶対パス。
    $resolvedBootstrapRoot = [System.IO.Path]::GetFullPath($bootstrapRoot).TrimEnd('\') + '\'
    $resolvedWorkRoot = [System.IO.Path]::GetFullPath($workRoot).TrimEnd('\') + '\'
    if (
        $resolvedWorkRoot.StartsWith(
            $resolvedBootstrapRoot,
            [System.StringComparison]::OrdinalIgnoreCase
        ) -and
        (Test-Path -LiteralPath $workRoot -PathType Container)
    ) {
        Remove-Item -LiteralPath $workRoot -Recurse -Force
    }
}
