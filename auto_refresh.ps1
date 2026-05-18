$PYTHON = "C:\Users\SPXPH5421\AppData\Local\Microsoft\WindowsApps\python.exe"
$ROOT   = "C:\Users\SPXPH5421\Documents\site scrapping"

Set-Location $ROOT

# Run scraper as a background job so we can push progress while it runs
$job = Start-Job -ScriptBlock {
    param($python, $root)
    Set-Location $root
    & $python run_scraper.py
} -ArgumentList $PYTHON, $ROOT

Write-Host "Scraper started (Job ID: $($job.Id)). Pushing live progress to GitHub every 90s..."

# Push status.json to GitHub every 90 seconds while scraper is running
while ($job.State -eq 'Running') {
    Start-Sleep 90
    git add data/status.json
    $staged = git diff --cached --name-only
    if ($staged) {
        git commit -m "Scraper progress $(Get-Date -Format 'HH:mm')"
        git push
        Write-Host "$(Get-Date -Format 'HH:mm:ss') — pushed status update"
    }
}

# Wait for job to fully finish
Wait-Job $job | Out-Null
Write-Host "Scraper finished. Pushing all data to GitHub..."

# Final push — all scraped data + final status
git add data/
git commit -m "Auto refresh listings $(Get-Date -Format 'yyyy-MM-dd')"
git push

Write-Host "Done. Dashboard will update shortly."
