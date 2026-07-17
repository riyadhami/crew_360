#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Build and push Docker images for Indigo Knowledge Graph agents
.DESCRIPTION
    This script builds Docker images for the agents and pushes them to Azure Container Registry
.PARAMETER AcrName
    Azure Container Registry name
.PARAMETER AgentNames
    Comma-separated list of agent names to build (default: all)
.EXAMPLE
    .\build-agents.ps1 -AcrName "myacr"
    .\build-agents.ps1 -AcrName "myacr" -AgentNames "graph-builder,graph-unifier"
#>

param(
    [Parameter(Mandatory = $true)]
    [string]$AcrName,
    
    [Parameter(Mandatory = $false)]
    [string]$AgentNames = "all"
)

$ErrorActionPreference = 'Stop'

Write-Host "═══════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  Indigo Knowledge Graph - Agent Build & Push" -ForegroundColor Cyan
Write-Host "═══════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host ""

# Get ACR login server
Write-Host "🔍 Getting ACR login server..." -ForegroundColor Yellow
$acrServer = az acr show --name $AcrName --query "loginServer" -o tsv
if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ Failed to get ACR login server" -ForegroundColor Red
    exit 1
}
Write-Host "✅ ACR Server: $acrServer" -ForegroundColor Green

# Login to ACR
Write-Host "🔐 Logging in to ACR..." -ForegroundColor Yellow
az acr login --name $AcrName
if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ Failed to login to ACR" -ForegroundColor Red
    exit 1
}
Write-Host "✅ Logged in to ACR" -ForegroundColor Green
Write-Host ""

# Define agents
$agents = @{
    "graph-builder" = @{
        "path" = "."
        "dockerfile" = "infra/Dockerfile.graph-builder"
        "description" = "Advanced Graph Builder Agent"
    }
    "graph-unifier" = @{
        "path" = "."
        "dockerfile" = "infra/Dockerfile.graph-unifier"
        "description" = "Graph Unification Agent"
    }
    "basic-inference" = @{
        "path" = "."
        "dockerfile" = "infra/Dockerfile.basic-inference"
        "description" = "Basic Inference Agent (Simple KG Queries)"
    }
    "standard-inference" = @{
        "path" = "."
        "dockerfile" = "infra/Dockerfile.standard-inference"
        "description" = "Standard Inference Agent (Single-Path Traversal)"
    }
    "tree-inference" = @{
        "path" = "."
        "dockerfile" = "infra/Dockerfile.tree-inference"
        "description" = "Tree Inference Agent (Multi-Threaded Exploration)"
    }
    "data-retrieval" = @{
        "path" = "."
        "dockerfile" = "infra/Dockerfile.data-retrieval"
        "description" = "Data Retrieval Agent (Physical DB Queries)"
    }
}

$agentsToBuild = @()
if ($AgentNames -eq "all") {
    $agentsToBuild = $agents.Keys
} else {
    $agentsToBuild = $AgentNames -split ','
}

# Build and push each agent
foreach ($agentName in $agentsToBuild) {
    if (-not $agents.ContainsKey($agentName)) {
        Write-Host "⚠️  Unknown agent: $agentName (skipping)" -ForegroundColor Yellow
        continue
    }
    
    $agent = $agents[$agentName]
    $imageName = "$acrServer/$agentName`:latest"
    
    Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Cyan
    Write-Host "🏗️  Building: $($agent.description)" -ForegroundColor Yellow
    Write-Host "   Image: $imageName" -ForegroundColor White
    Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Cyan
    Write-Host ""
    
    # Build image
    docker build `
        -f $agent.dockerfile `
        -t $imageName `
        $agent.path
    
    if ($LASTEXITCODE -ne 0) {
        Write-Host "❌ Failed to build $agentName" -ForegroundColor Red
        exit 1
    }
    
    Write-Host "✅ Built: $imageName" -ForegroundColor Green
    Write-Host ""
    
    # Push image
    Write-Host "📤 Pushing to ACR..." -ForegroundColor Yellow
    docker push $imageName
    
    if ($LASTEXITCODE -ne 0) {
        Write-Host "❌ Failed to push $agentName" -ForegroundColor Red
        exit 1
    }
    
    Write-Host "✅ Pushed: $imageName" -ForegroundColor Green
    Write-Host ""
}

Write-Host "═══════════════════════════════════════════════════════════" -ForegroundColor Green
Write-Host "  ✅ All agents built and pushed successfully!" -ForegroundColor Green
Write-Host "═══════════════════════════════════════════════════════════" -ForegroundColor Green
Write-Host ""
Write-Host "📋 Built images:" -ForegroundColor Cyan
foreach ($agentName in $agentsToBuild) {
    if ($agents.ContainsKey($agentName)) {
        Write-Host "   • $acrServer/$agentName`:latest" -ForegroundColor White
    }
}
Write-Host ""
Write-Host "Next: Run ./deploy-agents.ps1 to deploy to Foundry" -ForegroundColor Yellow
