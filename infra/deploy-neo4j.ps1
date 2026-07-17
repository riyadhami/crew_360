#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Deploy Neo4j to Azure Container Instances
.DESCRIPTION
    Deploys Neo4j in the same resource group as the main infrastructure
    Uses subscription 69642945-f464-4724-ba83-205eecbe5937
    Resource Group: Indigosetup_04232026
#>

[CmdletBinding()]
[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSAvoidUsingPlainTextForPassword', '')]
[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSUseDeclaredVarsMoreThanAssignments', '')]
param(
    [Parameter(Mandatory = $false)]
    [SecureString]$Neo4jPassword
)

$ErrorActionPreference = 'Stop'

# Pre-configured values
$SubscriptionId = "69642945-f464-4724-ba83-205eecbe5937"
$ResourceGroup = "Indigosetup_04232026"
$Location = "westus2"
$EnvironmentName = "dev"
$ProjectName = "indigo-kg"

Write-Host "═══════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  Deploy Neo4j to Azure Container Instances" -ForegroundColor Cyan
Write-Host "═══════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host ""

# Get Neo4j password if not provided
if (-not $Neo4jPassword) {
    Write-Host "⚠️  Neo4j password is required (minimum 8 characters)" -ForegroundColor Yellow
    $Neo4jPassword = Read-Host "Enter Neo4j password" -AsSecureString
}

# Convert SecureString to plain text for deployment
$BSTR = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($Neo4jPassword)
$neo4jPasswordPlain = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($BSTR)
[System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($BSTR)

if ($neo4jPasswordPlain.Length -lt 8) {
    Write-Host "❌ Password must be at least 8 characters" -ForegroundColor Red
    exit 1
}

# Set subscription
Write-Host "🔄 Setting subscription..." -ForegroundColor Yellow
az account set --subscription $SubscriptionId

$account = az account show | ConvertFrom-Json
Write-Host "✅ Using subscription: $($account.name)" -ForegroundColor Green
Write-Host ""

# Display configuration
Write-Host "📋 Neo4j Deployment Configuration:" -ForegroundColor Yellow
Write-Host "   Subscription   : $SubscriptionId" -ForegroundColor White
Write-Host "   Resource Group : $ResourceGroup" -ForegroundColor White
Write-Host "   Location       : $Location" -ForegroundColor White
Write-Host "   Environment    : $EnvironmentName" -ForegroundColor White
Write-Host ""

$confirm = Read-Host "Deploy Neo4j? (y/N)"
if ($confirm -ne 'y' -and $confirm -ne 'Y') {
    Write-Host "❌ Deployment cancelled" -ForegroundColor Red
    exit 0
}

# Deploy Neo4j
Write-Host ""
Write-Host "🚀 Deploying Neo4j (this may take 5-10 minutes)..." -ForegroundColor Yellow
Write-Host ""

$deploymentName = "neo4j-deploy-$(Get-Date -Format 'yyyyMMdd-HHmmss')"

$deployment = az deployment group create `
    --name $deploymentName `
    --resource-group $ResourceGroup `
    --template-file "$PSScriptRoot/neo4j-aci.bicep" `
    --parameters environmentName=$EnvironmentName `
    --parameters projectName=$ProjectName `
    --parameters neo4jPassword=$neo4jPasswordPlain `
    --output json | ConvertFrom-Json

if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ Deployment failed" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "═══════════════════════════════════════════════════════════" -ForegroundColor Green
Write-Host "  ✅ Neo4j Deployed Successfully!" -ForegroundColor Green
Write-Host "═══════════════════════════════════════════════════════════" -ForegroundColor Green
Write-Host ""

# Display outputs
Write-Host "📊 Neo4j Connection Details:" -ForegroundColor Cyan
Write-Host ""
Write-Host "🌐 Browser UI:" -ForegroundColor Yellow
Write-Host "   URL           : $($deployment.properties.outputs.neo4jBrowserUrl.value)" -ForegroundColor White
Write-Host ""
Write-Host "⚡ Bolt Connection:" -ForegroundColor Yellow
Write-Host "   URL           : $($deployment.properties.outputs.neo4jBoltUrl.value)" -ForegroundColor White
Write-Host "   FQDN          : $($deployment.properties.outputs.neo4jFqdn.value)" -ForegroundColor White
Write-Host "   IP Address    : $($deployment.properties.outputs.neo4jIpAddress.value)" -ForegroundColor White
Write-Host ""
Write-Host "🔐 Credentials:" -ForegroundColor Yellow
Write-Host "   Username      : $($deployment.properties.outputs.neo4jUser.value)" -ForegroundColor White
Write-Host "   Password      : [The password you provided]" -ForegroundColor White
Write-Host ""
Write-Host "💾 Storage:" -ForegroundColor Yellow
Write-Host "   Account Name  : $($deployment.properties.outputs.storageAccountName.value)" -ForegroundColor White
Write-Host ""

# Update .env file
$envFile = "$PSScriptRoot/../.env"
if (Test-Path $envFile) {
    Write-Host "📝 Updating .env with Neo4j connection details..." -ForegroundColor Yellow
    
    # Read current .env content
    $currentEnv = Get-Content $envFile -Raw
    
    # Update or add Neo4j values
    $updates = @{
        'NEO4J_URI' = $deployment.properties.outputs.neo4jBoltUrl.value
        'NEO4J_USER' = $deployment.properties.outputs.neo4jUser.value
        'NEO4J_PASSWORD' = $neo4jPasswordPlain
    }
    
    foreach ($key in $updates.Keys) {
        $value = $updates[$key]
        if ($currentEnv -match "(?m)^$key=.*$") {
            $currentEnv = $currentEnv -replace "(?m)^$key=.*$", "$key=$value"
        } else {
            $currentEnv += "`n$key=$value"
        }
    }
    
    $currentEnv | Out-File $envFile -NoNewline
    Write-Host "✅ Updated $envFile" -ForegroundColor Green
} else {
    Write-Host "⚠️  .env not found in root directory." -ForegroundColor Yellow
}

# Save Neo4j outputs
$neo4jOutputsFile = "$PSScriptRoot/../.azure/neo4j-outputs.json"
$deployment.properties.outputs | ConvertTo-Json -Depth 10 | Set-Content $neo4jOutputsFile
Write-Host "💾 Neo4j outputs saved to: $neo4jOutputsFile" -ForegroundColor Green
Write-Host ""

# Display next steps
Write-Host "═══════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  📚 Next Steps" -ForegroundColor Cyan
Write-Host "═══════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host ""
Write-Host "1. Access Neo4j Browser:" -ForegroundColor Yellow
Write-Host "   Open: $($deployment.properties.outputs.neo4jBrowserUrl.value)" -ForegroundColor White
Write-Host "   Login with username 'neo4j' and your password" -ForegroundColor White
Write-Host ""
Write-Host "2. Test connection:" -ForegroundColor Yellow
Write-Host "   python -c `"from src.utils.neo4j_helpers import get_neo4j_driver; d = get_neo4j_driver(); print('✅ Connected'); d.close()`"" -ForegroundColor White
Write-Host ""
Write-Host "3. Run your agents (your .env file is already updated):" -ForegroundColor Yellow
Write-Host "   python -m src.agents.advanced_graph_builder_agent --database all" -ForegroundColor White
Write-Host ""
Write-Host "═══════════════════════════════════════════════════════════" -ForegroundColor Green
Write-Host ""
Write-Host "⚠️  Note: Neo4j is running on a public IP. For production, configure VNet integration." -ForegroundColor Yellow
Write-Host "🎉 Neo4j is ready! You can now run your agents without code changes." -ForegroundColor Green
