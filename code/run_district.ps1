<#
Run the district pipeline: prepare -> recom (3 maps) -> audit -> render -> booth_mip.
Same steps and options as run_district.sh.

  .\run_district.ps1 -DistrictId 531 -Config district_config/sangli.json `
      -Census raw/DDW_PCA2735_2011_MDDS.xlsx -Villages raw/shrug_pc11_village.gpkg `
      -Boundaries raw/shrug_pc11_district.gpkg -Subdistricts raw/shrug_pc11_subdistrict.gpkg `
      -Out runs/sangli

Reuse an existing input folder instead of preparing one:
  .\run_district.ps1 -UnitsDir sangli_input -Config district_config/sangli.json -Out runs/sangli
#>
param(
    [Parameter(Mandatory = $true)][string]$Out,
    [string]$StateId = "27",
    [string]$DistrictId = "",
    [string]$Config = "",
    [int]$Seats = 0,
    [string]$Allocation = "",
    [string]$CurrentDistrict = "",
    [string]$Census = "",
    [string]$Villages = "",
    [string]$Boundaries = "",
    [string]$Subdistricts = "",
    [string]$UnitsDir = "",
    [string[]]$Maps = @("balanced", "locality", "taluka"),
    [int]$Steps = 300,
    [int]$Restarts = 0,  # 0 = the pilot's value per map (balanced 2, locality 2, taluka 1)
    [string]$DistanceMatrix = "",
    [double[]]$MaxKm = @(2, 3, 5),
    [double]$BoothTimeLimit = 300,
    [switch]$SkipBooth,
    [string]$Python = "python"
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Step($text) { Write-Host ""; Write-Host "=== $text ===" }
function Run([string[]]$cmd) {
    & $Python @cmd
    if ($LASTEXITCODE -ne 0) { throw "step failed (exit $LASTEXITCODE): python $($cmd -join ' ')" }
}
function ReadJson($path, $key) {
    if (-not (Test-Path $path)) { return "" }
    $value = (Get-Content $path -Raw -Encoding UTF8 | ConvertFrom-Json).$key
    if ($null -eq $value) { return "" } else { return "$value" }
}

# Weights population/distance/taluka/shape, seed and restarts for each map
# (the Sangli pilot's settings, which reproduce its stored maps).
$Weights = @{
    balanced = @("0.35", "0.30", "0.20", "0.15", "303", "2")
    locality = @("0.30", "0.45", "0.10", "0.15", "714", "2")
    taluka   = @("0.30", "0.20", "0.35", "0.15", "303", "1")
}
$Maps = @($Maps | ForEach-Object { $_ -split "," } | Where-Object { $_ })
foreach ($m in $Maps) { if (-not $Weights.ContainsKey($m)) { throw "unknown map $m (balanced, locality, taluka)" } }

# 1. prepare
if (-not $UnitsDir) {
    if (-not $DistrictId) { throw "-DistrictId is required to prepare inputs (or pass -UnitsDir)" }
    $UnitsDir = Join-Path $Out "input"
    $a = @("prepare_district_inputs.py", "--state-id", $StateId, "--district-id", $DistrictId, "--out", $UnitsDir)
    if ($Census) { $a += @("--census", $Census) }
    if ($Villages) { $a += @("--villages", $Villages) }
    if ($Boundaries) { $a += @("--district", $Boundaries) }
    if ($Subdistricts) { $a += @("--subdistricts", $Subdistricts) }
    if ($Config) { $a += @("--config", $Config) }
    if ($Seats) { $a += @("--seats", "$Seats") }
    if ($Allocation) { $a += @("--allocation", $Allocation) }
    if ($CurrentDistrict) { $a += @("--current-district", $CurrentDistrict) }
    Step "prepare"
    Run $a
}
$unitFiles = @(Get-ChildItem -Path $UnitsDir -Filter "*_units.geojson")
$boundFiles = @(Get-ChildItem -Path $UnitsDir -Filter "*_district.geojson")
if ($unitFiles.Count -ne 1) { throw "expected one *_units.geojson in $UnitsDir, found $($unitFiles.Count)" }
if ($boundFiles.Count -ne 1) { throw "expected one *_district.geojson in $UnitsDir, found $($boundFiles.Count)" }
$Units = $unitFiles[0].FullName
$Boundary = $boundFiles[0].FullName

# Seats and footer: -Seats, then input_audit.json, then the config.
$SeatText = if ($Seats) { "$Seats" } else { ReadJson (Join-Path $UnitsDir "input_audit.json") "seats" }
if (-not $SeatText -and $Config) { $SeatText = ReadJson $Config "seats" }
if (-not $SeatText) { throw "seat count unknown: pass -Seats or a -Config with `"seats`"" }
$Sources = if ($Config) { ReadJson $Config "sources" } else { "" }
Write-Host "units: $Units"; Write-Host "district: $Boundary"; Write-Host "seats: $SeatText"

# 2. recom
$dirs = @()
foreach ($m in $Maps) {
    $w = $Weights[$m]
    $dir = Join-Path $Out "map_$m"
    Step "recom map_$m (weights $($w[0])/$($w[1])/$($w[2])/$($w[3]), seed $($w[4]))"
    $a = @("sangli_recom.py", "optimize", "--units", $Units, "--district", $Boundary, "--out", $dir,
           "--seats", $SeatText, "--weight-population", $w[0], "--weight-distance", $w[1],
           "--weight-taluka", $w[2], "--weight-shape", $w[3], "--steps", "$Steps",
           "--restarts", $(if ($Restarts) { "$Restarts" } else { $w[5] }), "--seed", $w[4])
    if ($DistanceMatrix) { $a += @("--distance-matrix", $DistanceMatrix) }
    Run $a
    $dirs += $dir
}

# 3. audit
Step "audit"
Run (@("audit_sangli_maps.py", "--input", $Units, "--district", $Boundary, "--results") + $dirs +
     @("--out", $Out, "--seats", $SeatText))

# 4. render (colours aligned to the first map)
foreach ($dir in $dirs) {
    Step "render $(Split-Path $dir -Leaf)"
    $a = @("render_sangli_maps.py", "--result", $dir, "--units", $Units, "--district", $Boundary, "--out", "$dir.png")
    if ($dir -ne $dirs[0]) { $a += @("--reference", $dirs[0]) }
    if ($Subdistricts) { $a += @("--talukas", $Subdistricts, "--district-id", $DistrictId) }
    if ($Sources) { $a += @("--sources", $Sources) }
    Run $a
}

# 5. booth MIP
if (-not $SkipBooth) {
    Step "booth_mip"
    $a = @("booth_mip.py", "--assigned") + ($dirs | ForEach-Object { Join-Path $_ "assigned_units.geojson" })
    $a += @("--max-km") + ($MaxKm | ForEach-Object { "$_" })
    $a += @("--time-limit", "$BoothTimeLimit", "--out", (Join-Path $Out "booth_mip_results"))
    if ($DistanceMatrix) { $a += @("--distance-matrix", $DistanceMatrix) }
    Run $a
}
Step "done: $Out"
