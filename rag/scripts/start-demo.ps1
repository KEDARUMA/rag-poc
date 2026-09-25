[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8765
)

# 定数: ローカルで起動したRAG検索デモのURL。
$DemoUrl = "http://localhost:$Port/"

Write-Host "RAG検索デモを開きます: $DemoUrl"
Write-Host 'サーバーが起動していない場合は、先に start-server.ps1 を実行してください。'
Start-Process -FilePath $DemoUrl
