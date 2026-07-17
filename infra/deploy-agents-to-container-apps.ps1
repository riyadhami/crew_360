#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Deploy agents to Azure Container Apps (alternative to Foundry)
.DESCRIPTION
    Deploys knowledge graph agents as HTTP services using Azure Container Apps,
    which doesn't have the storage authentication restrictions of Azure ML.
.EXAMPLE
    .\deploy-agents-to-container-apps.ps1
#>

$ErrorActionPreference = 'Stop'

Write-Host ""
Write-Host "╔═══════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║                                                           ║" -ForegroundColor Cyan
Write-Host "║     Deploy Agents to Azure Container Apps                ║" -ForegroundColor Cyan
Write-Host "║                                                           ║" -ForegroundColor Cyan
Write-Host "╚═══════════════════════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

# Load config
$envPath = "$PSScriptRoot/../.env"
$config = @{}
Get-Content $envPath | ForEach-Object {
    $line = $_.Trim()
    if ($line -and !$line.StartsWith('#')) {
        $parts = $line -split '=', 2
        if ($parts.Count -eq 2) {
            $config[$parts[0].Trim()] = $parts[1].Trim()
        }
    }
}

$resourceGroup = $config['AZURE_RESOURCE_GROUP']
$location = $config['AZURE_LOCATION']
$acrName = $config['AZURE_CONTAINER_REGISTRY']
$acrLoginServer = "$acrName.azurecr.io"

Write-Host "📋 Configuration:" -ForegroundColor Cyan
Write-Host "   Resource Group: $resourceGroup" -ForegroundColor White
Write-Host "   Location: $location" -ForegroundColor White
Write-Host "   ACR: $acrName" -ForegroundColor White
Write-Host ""

# Create Container Apps environment
Write-Host "🌐 Creating Container Apps environment..." -ForegroundColor Yellow
$envName = "indigo-kg-env"

az containerapp env show --name $envName --resource-group $resourceGroup --output none 2>$null
if ($LASTEXITCODE -ne 0) {
    az containerapp env create `
        --name $envName `
        --resource-group $resourceGroup `
        --location $location `
        --output none
    
    Write-Host "✅ Environment created" -ForegroundColor Green
} else {
    Write-Host "✅ Environment already exists" -ForegroundColor Green
}
Write-Host ""

# Get ACR credentials
Write-Host "🔑 Getting ACR credentials..." -ForegroundColor Yellow
$acrUsername = az acr credential show --name $acrName --query username --output tsv
$acrPassword = az acr credential show --name $acrName --query passwords[0].value --output tsv
Write-Host "✅ Credentials retrieved" -ForegroundColor Green
Write-Host ""

# Define agents to deploy
$agents = @(
    @{
        name = "indigo-kg-builder"
        image = "graph-builder"
        description = "Graph Builder Agent"
        cpu = "1.0"
        memory = "2.0Gi"
        timeout = "300"
    },
    @{
        name = "indigo-kg-unifier"
        image = "graph-unifier"
        description = "Graph Unifier Agent"
        cpu = "1.0"
        memory = "2.0Gi"
        timeout = "300"
    },
    @{
        name = "indigo-kg-basic-inference"
        image = "basic-inference"
        description = "Basic Inference Agent"
        cpu = "0.5"
        memory = "1.0Gi"
        timeout = "300"
    },
    @{
        name = "indigo-kg-standard-inference"
        image = "standard-inference"
        description = "Standard Inference Agent"
        cpu = "0.5"
        memory = "1.0Gi"
        timeout = "300"
    },
    @{
        name = "indigo-kg-tree-inference"
        image = "tree-inference"
        description = "Tree Inference Agent (Multi-threaded)"
        cpu = "1.0"
        memory = "2.0Gi"
        timeout = "600"
    },
    @{
        name = "indigo-kg-data-retrieval"
        image = "data-retrieval"
        description = "Data Retrieval Agent"
        cpu = "1.0"
        memory = "2.0Gi"
        timeout = "600"
        requiresSql = $true
    }
)

$deployedUrls = @{}

# Deploy each agent
foreach ($agent in $agents) {
    Write-Host "📦 Deploying $($agent.description)..." -ForegroundColor Yellow
    
    # Build environment variables
    $envVars = "AZURE_OPENAI_ENDPOINT=$($config['AZURE_OPENAI_ENDPOINT'])"
    $envVars += " AZURE_OPENAI_API_KEY=secretref:openai-key"
    $envVars += " COSMOS_ENDPOINT=$($config['COSMOS_ENDPOINT'])"
    $envVars += " COSMOS_KEY=secretref:cosmos-key"
    $envVars += " COSMOS_DB_ENDPOINT=$($config['COSMOS_ENDPOINT'])"
    $envVars += " COSMOS_DB_KEY=secretref:cosmos-key"
    $envVars += " COSMOS_DB_DATABASE=IndigoKG"
    $envVars += " COSMOS_DB_GRAPH=unified_graph"
    
    # Add SQL Server config for data retrieval agent
    if ($agent.requiresSql) {
        $envVars += " SQL_SERVER=$($config['SQL_SERVER_HOST'])"
        $envVars += " SQL_USER=$($config['SQL_SERVER_USER'])"
        $envVars += " SQL_PASSWORD=secretref:sql-password"
    }
    
    # Build secrets
    $secrets = "openai-key=$($config['AZURE_OPENAI_API_KEY'])"
    $secrets += " cosmos-key=$($config['COSMOS_KEY'])"
    if ($agent.requiresSql -and $config['SQL_SERVER_PASSWORD']) {
        $secrets += " sql-password=$($config['SQL_SERVER_PASSWORD'])"
    }
    
    az containerapp create `
        --name $agent.name `
        --resource-group $resourceGroup `
        --environment $envName `
        --image "$acrLoginServer/$($agent.image):latest" `
        --target-port 8080 `
        --ingress external `
        --registry-server $acrLoginServer `
        --registry-username $acrUsername `
        --registry-password $acrPassword `
        --cpu $agent.cpu `
        --memory $agent.memory `
        --min-replicas 1 `
        --max-replicas 3 `
        --env-vars $envVars `
        --secrets $secrets `
        --output none 2>&1 | Out-Null
    
    if ($LASTEXITCODE -eq 0) {
        Write-Host "✅ $($agent.description) deployed" -ForegroundColor Green
        
        $url = az containerapp show `
            --name $agent.name `
            --resource-group $resourceGroup `
            --query properties.configuration.ingress.fqdn `
            --output tsv
        
        $deployedUrls[$agent.name] = "https://$url"
        Write-Host "   URL: https://$url" -ForegroundColor Cyan
    } else {
        Write-Host "❌ Failed to deploy $($agent.description)" -ForegroundColor Red
    }
    Write-Host ""
}

Write-Host "╔═══════════════════════════════════════════════════════════╗" -ForegroundColor Green
Write-Host "║                                                           ║" -ForegroundColor Green
Write-Host "║           🎉 Deployment Complete! 🎉                      ║" -ForegroundColor Green
Write-Host "║                                                           ║" -ForegroundColor Green
Write-Host "╚═══════════════════════════════════════════════════════════╝" -ForegroundColor Green
Write-Host ""

Write-Host "📊 Deployed Services:" -ForegroundColor Cyan
foreach ($key in $deployedUrls.Keys | Sort-Object) {
    Write-Host "   • $key : $($deployedUrls[$key])" -ForegroundColor White
}
Write-Host ""

Write-Host "🧪 Test your services:" -ForegroundColor Cyan
Write-Host ""
Write-Host "Infrastructure Agents:" -ForegroundColor Yellow
Write-Host '   # Graph Builder' -ForegroundColor Gray
Write-Host '   $body = @{database="CLMS"; skip_neo4j=$true} | ConvertTo-Json' -ForegroundColor White
Write-Host "   Invoke-RestMethod -Uri $($deployedUrls['indigo-kg-builder'])/score -Method POST -Body `$body -ContentType 'application/json'" -ForegroundColor White
Write-Host ""
Write-Host '   # Graph Unifier' -ForegroundColor Gray
Write-Host '   $body = @{graph1="output/db1.json"; graph2="output/db2.json"} | ConvertTo-Json' -ForegroundColor White
Write-Host "   Invoke-RestMethod -Uri $($deployedUrls['indigo-kg-unifier'])/score -Method POST -Body `$body -ContentType 'application/json'" -ForegroundColor White
Write-Host ""
Write-Host "Query/Inference Agents:" -ForegroundColor Yellow
Write-Host '   # Basic Inference' -ForegroundColor Gray
Write-Host '   $body = @{query="What tables are in CLMS?"; backend="cosmos"} | ConvertTo-Json' -ForegroundColor White
Write-Host "   Invoke-RestMethod -Uri $($deployedUrls['indigo-kg-basic-inference'])/score -Method POST -Body `$body -ContentType 'application/json'" -ForegroundColor White
Write-Host ""
Write-Host '   # Data Retrieval' -ForegroundColor Gray
Write-Host '   $body = @{query="Get employee performance for Akash Saxena"; backend="cosmos"} | ConvertTo-Json' -ForegroundColor White
Write-Host "   Invoke-RestMethod -Uri $($deployedUrls['indigo-kg-data-retrieval'])/score -Method POST -Body `$body -ContentType 'application/json'" -ForegroundColor White
Write-Host ""
