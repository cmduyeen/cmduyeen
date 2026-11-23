# Verification Contract cho cmduyeen-github-profile
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

Write-Host "`n=== VERIFY: cmduyeen-github-profile ===" -ForegroundColor Cyan

# 1. Python syntax + unit tests
& python -m py_compile "$root\.github\scripts\update_profile.py" "$root\.github\scripts\test_update_profile.py"
if ($LASTEXITCODE -ne 0) { exit 1 }
Write-Host "  [PASS] Python py_compile" -ForegroundColor Green

& python -m unittest discover -s "$root\.github\scripts" -p "test_*.py" -v
if ($LASTEXITCODE -ne 0) { exit 1 }
Write-Host "  [PASS] Unit tests" -ForegroundColor Green

# 2. README minimalism check (markers present, no <img src=assets/ bloat)
$readme = Get-Content "$root\README.md" -Raw
$hasMarkers = ($readme -match "<!-- MY-STUFF:START -->") -and ($readme -match "<!-- BLOG-POST-LIST:START -->")
$noSvgBloat = $readme -notmatch "<img\s+src=`"assets/"
if (-not ($hasMarkers -and $noSvgBloat)) {
    Write-Host "  [FAIL] README.md markers or minimalism check failed" -ForegroundColor Red
    exit 1
}
Write-Host "  [PASS] README.md minimal & markers valid" -ForegroundColor Green
Write-Host "✅ cmduyeen-github-profile VERIFIED: 0 lỗi.`n" -ForegroundColor Green
exit 0
