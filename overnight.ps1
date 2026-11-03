# Runs every remaining evaluation in order, unattended.
# Start it from the offtrav folder, in the same PowerShell window you normally use:
#     Set-ExecutionPolicy -Scope Process Bypass
#     .\overnight.ps1
# Phase 1 uses the pipeline as tested (before the fixes). Phase 2 installs the fixes and re-runs.
# Everything is written to runs\ (git-ignored). The log is runs\overnight_log.txt.

param([string]$Update = "$env:USERPROFILE\Downloads\offtrav_update.zip")

$ErrorActionPreference = "Continue"
New-Item -ItemType Directory -Force runs | Out-Null
Start-Transcript -Path "runs\overnight_log.txt" -Append | Out-Null

function Run([string]$Name, [string[]]$CmdArgs) {
    Write-Host ""
    Write-Host "=== $(Get-Date -Format 'yyyy-MM-dd HH:mm')  $Name ==="
    & python @CmdArgs
    Write-Host "exit code: $LASTEXITCODE"
}

$Synth   = @("--index", "index\synthetic", "--cases", "eval\cases_synthetic.jsonl")
$Private = @("--index", "index\private",   "--cases", "eval\cases_private.jsonl")

# ---- checks before starting
foreach ($p in @("index\synthetic", "index\private", "eval\cases_private.jsonl")) {
    if (-not (Test-Path $p)) { Write-Host "Missing $p. Finish the private set-up steps first."; Stop-Transcript | Out-Null; exit 1 }
}
& ollama list | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Host "Ollama is not running. Start it and try again."; Stop-Transcript | Out-Null; exit 1 }

# ---- phase 1: pipeline as tested
Run "1/8 private dev (as tested)"                 (@("eval\run_eval.py") + $Private + @("--split", "dev",  "--label", "private_dev"))
Run "2/8 private test (as tested)"                (@("eval\run_eval.py") + $Private + @("--split", "test", "--label", "private"))
Run "3/8 synthetic test, full-context condition"  (@("eval\run_eval.py") + $Synth   + @("--split", "test", "--modes", "full_context", "--label", "full_context"))
Run "4/8 synthetic test, search comparison"       (@("eval\run_eval.py") + $Synth   + @("--split", "test", "--retrieval-only", "--label", "retrieval_ablation"))

# ---- install the fixes
Write-Host ""
Write-Host "=== $(Get-Date -Format 'yyyy-MM-dd HH:mm')  installing the fixes ==="
if (-not (Test-Path $Update)) { Write-Host "Update not found at $Update. Phase 2 skipped."; Stop-Transcript | Out-Null; exit 1 }
Expand-Archive -Path $Update -DestinationPath . -Force
& python -m pytest -q
if ($LASTEXITCODE -ne 0) { Write-Host "Tests failed after the update. Phase 2 skipped."; Stop-Transcript | Out-Null; exit 1 }

# ---- phase 2: revised pipeline
Run "5/8 synthetic dev (after fixes)"             (@("eval\run_eval.py") + $Synth   + @("--split", "dev",      "--label", "after_fixes_dev"))
Run "6/8 synthetic fix-check (after fixes)"       (@("eval\run_eval.py") + $Synth   + @("--split", "fixcheck", "--label", "after_fixes_fixcheck"))
Run "7/8 synthetic test, already seen"            (@("eval\run_eval.py") + $Synth   + @("--split", "test",     "--label", "after_fixes_test_seen"))
Run "8/8 private test (after fixes)"              (@("eval\run_eval.py") + $Private + @("--split", "test",     "--label", "private_after_fixes"))

Write-Host ""
Write-Host "=== $(Get-Date -Format 'yyyy-MM-dd HH:mm')  all done ==="
Stop-Transcript | Out-Null
