#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Register Container App endpoints as custom connections in Azure AI Foundry
.DESCRIPTION
    Creates custom connections in Foundry workspace for all deployed Container Apps
#>

param(
    [Parameter(Mandatory = $false)]
    [string]$WorkspaceName = "mannamraju-1328",
    
    [Parameter(Mandatory = $false)]
    [string]$ResourceGroup = "rg-gpt4o-ft-x5o6m1",
    
    [Parameter(Mandatory = $false)]
    [string]$BaseUrl = "https://proudocean-a4621ff3.westus2.azurecontainerapps.io"
)

$ErrorActionPreference = 'Stop'

Write-Host "╔═══════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║  Register Container Apps as Foundry Custom Connections   ║" -ForegroundColor Cyan
Write-Host "╚═══════════════════════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

Write-Host "📋 Configuration:" -ForegroundColor Yellow
Write-Host "   Workspace: $WorkspaceName" -ForegroundColor White
Write-Host "   Resource Group: $ResourceGroup" -ForegroundColor White
Write-Host "   Base URL: $BaseUrl" -ForegroundColor White
Write-Host ""

# Define all agents to register
$agents = @(
    @{
        name = "Graph Builder"
        yaml = "connection-graph-builder.yml"
        connection = "indigo-kg-builder"
    },
    @{
        name = "Graph Unifier"
        yaml = "connection-graph-unifier.yml"
        connection = "indigo-kg-unifier"
    },
    @{
        name = "Basic Inference"
        yaml = "connection-basic-inference.yml"
        connection = "indigo-kg-basic-inference"
    },
    @{
        name = "Standard Inference"
        yaml = "connection-standard-inference.yml"
        connection = "indigo-kg-standard-inference"
    },
    @{
        name = "Tree Inference"
        yaml = "connection-tree-inference.yml"
        connection = "indigo-kg-tree-inference"
    },
    @{
        name = "Data Retrieval"
        yaml = "connection-data-retrieval.yml"
        connection = "indigo-kg-data-retrieval"
    }
)

$registered = @()
$failed = @()

# Register each agent connection
foreach ($agent in $agents) {
    Write-Host "🔗 Registering $($agent.name) connection..." -ForegroundColor Yellow
    
    $yamlPath = "$PSScriptRoot/$($agent.yaml)"
    
    if (-not (Test-Path $yamlPath)) {
        Write-Host "   ⚠️  YAML file not found: $yamlPath" -ForegroundColor Yellow
        $failed += $agent.name
        Write-Host ""
        continue
    }
    
    try {
        az ml connection create `
            --file $yamlPath `
            --resource-group $ResourceGroup `
            --workspace-name $WorkspaceName `
            --output table 2>&1 | Out-Null
        
        if ($LASTEXITCODE -eq 0) {
            Write-Host "   ✅ $($agent.name) connection registered" -ForegroundColor Green
            $registered += $agent.connection
        } else {
            Write-Host "   ⚠️  Connection may already exist or registration failed" -ForegroundColor Yellow
            $failed += $agent.name
        }
    } catch {
        Write-Host "   ⚠️  Error registering $($agent.name): $_" -ForegroundColor Yellow
        $failed += $agent.name
    }
    
    Write-Host ""
}

Write-Host "╔═══════════════════════════════════════════════════════════╗" -ForegroundColor Green
Write-Host "║              ✅ Registration Complete!                    ║" -ForegroundColor Green
Write-Host "╚═══════════════════════════════════════════════════════════╝" -ForegroundColor Green
Write-Host ""

Write-Host "📊 Summary:" -ForegroundColor Cyan
Write-Host "   ✅ Successfully registered: $($registered.Count) agents" -ForegroundColor Green
if ($registered.Count -gt 0) {
    foreach ($conn in $registered) {
        Write-Host "      • $conn" -ForegroundColor White
    }
}
if ($failed.Count -gt 0) {
    Write-Host "   ⚠️  Failed or skipped: $($failed.Count) agents" -ForegroundColor Yellow
    foreach ($name in $failed) {
        Write-Host "      • $name" -ForegroundColor White
    }
}
Write-Host ""

Write-Host "🎯 Next Steps:" -ForegroundColor Cyan
Write-Host "   1. Go to Azure AI Foundry Studio: https://ai.azure.com/" -ForegroundColor White
Write-Host "   2. Open project: $WorkspaceName" -ForegroundColor White
Write-Host "   3. Navigate to: Management → Connections" -ForegroundColor White
Write-Host "   4. View your registered connections" -ForegroundColor White
Write-Host ""

Write-Host "💡 Usage in Prompt Flow:" -ForegroundColor Cyan
Write-Host "   Infrastructure Agents:" -ForegroundColor Yellow
Write-Host "   - indigo-kg-builder (graph building from schemas)" -ForegroundColor White
Write-Host "   - indigo-kg-unifier (merge multiple graphs)" -ForegroundColor White
Write-Host ""
Write-Host "   Query/Inference Agents:" -ForegroundColor Yellow
Write-Host "   - indigo-kg-basic-inference (simple KG queries)" -ForegroundColor White
Write-Host "   - indigo-kg-standard-inference (single-path traversal)" -ForegroundColor White
Write-Host "   - indigo-kg-tree-inference (multi-threaded exploration)" -ForegroundColor White
Write-Host "   - indigo-kg-data-retrieval (physical DB queries)" -ForegroundColor White
Write-Host ""
