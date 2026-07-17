#!/usr/bin/env pwsh
<#
.SYNOPSIS
    End-to-End deployment for Indigo Knowledge Graph project
.DESCRIPTION
    Complete automated deployment that:
    - Uses existing Azure AI Foundry project from .env
    - Deploys infrastructure (Cosmos DB, ACR, Key Vault, Monitoring)
    - Optionally deploys Neo4j database
    - Updates .env with all connection details
.EXAMPLE
    .\deploy-all.ps1
    .\deploy-all.ps1 -SkipNeo4j
    $pwd = ConvertTo-SecureString "MySecurePass123" -AsPlainText -Force
    .\deploy-all.ps1 -Neo4jPassword $pwd
#>

[CmdletBinding()]
[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSAvoidUsingPlainTextForPassword', 'Neo4jPassword')]
[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSUseDeclaredVarsMoreThanAssignments', '')]
param(
    [switch]$SkipNeo4j,
    [SecureString]$Neo4jPassword
)

$ErrorActionPreference = 'Stop'

# ============================================================================
# Banner
# ============================================================================
Clear-Host
Write-Host ""
Write-Host "╔═══════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║                                                           ║" -ForegroundColor Cyan
Write-Host "║     Indigo Knowledge Graph - End-to-End Deployment       ║" -ForegroundColor Cyan
Write-Host "║                                                           ║" -ForegroundColor Cyan
Write-Host "╚═══════════════════════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

# ============================================================================
# Helper Functions
# ============================================================================
function Read-EnvFile {
    param([string]$Path)
    
    $config = @{}
    if (Test-Path $Path) {
        Get-Content $Path | ForEach-Object {
            $line = $_.Trim()
            if ($line -and !$line.StartsWith('#')) {
                $parts = $line -split '=', 2
                if ($parts.Count -eq 2) {
                    $key = $parts[0].Trim()
                    $value = $parts[1].Trim()
                    $config[$key] = $value
                }
            }
        }
    }
    return $config
}

function Update-EnvFile {
    param(
        [string]$Path,
        [hashtable]$Updates
    )
    
    $currentEnv = Get-Content $Path -Raw
    
    foreach ($key in $Updates.Keys) {
        $value = $Updates[$key]
        if ($currentEnv -match "(?m)^$key=.*$") {
            $currentEnv = $currentEnv -replace "(?m)^$key=.*$", "$key=$value"
        } else {
            $currentEnv += "`n$key=$value"
        }
    }
    
    $currentEnv | Out-File $Path -NoNewline
}

# ============================================================================
# Step 1: Load and Validate Configuration
# ============================================================================
Write-Host "📄 Step 1: Loading configuration from .env..." -ForegroundColor Yellow
Write-Host ""

$envPath = "$PSScriptRoot/../.env"
if (-not (Test-Path $envPath)) {
    Write-Host "❌ .env file not found at: $envPath" -ForegroundColor Red
    Write-Host ""
    Write-Host "Please create a .env file in the root directory." -ForegroundColor Yellow
    exit 1
}

$config = Read-EnvFile -Path $envPath

# ============================================================================
# Determine Deployment Mode
# ============================================================================
$useExistingFoundry = $false
if ($config['USE_EXISTING_FOUNDRY_PROJECT']) {
    $useExistingFoundry = $config['USE_EXISTING_FOUNDRY_PROJECT'] -eq 'true'
    Write-Host "📄 Deployment mode from .env: $(if ($useExistingFoundry) {'Use Existing Foundry'} else {'Create New Foundry'})" -ForegroundColor Cyan
} else {
    # Prompt user if not set in .env
    Write-Host ""
    Write-Host "❓ Azure AI Foundry Deployment Mode" -ForegroundColor Yellow
    Write-Host "   Do you want to use an existing Azure AI Foundry project?" -ForegroundColor White
    Write-Host "   - Y = Use existing project (requires project details in .env)" -ForegroundColor Gray
    Write-Host "   - N = Create new project (will provision new AI Hub/Project)" -ForegroundColor Gray
    Write-Host ""
    $response = Read-Host "   Enter choice [Y/n]"
    $useExistingFoundry = ($response -ne 'n' -and $response -ne 'N')
    
    # Update .env with user's choice
    Write-Host "   Saving choice to .env..." -ForegroundColor Gray
    Update-EnvFile -Path $envPath -Updates @{ 'USE_EXISTING_FOUNDRY_PROJECT' = $useExistingFoundry.ToString().ToLower() }
    Write-Host "   ✅ Saved USE_EXISTING_FOUNDRY_PROJECT=$($useExistingFoundry.ToString().ToLower())" -ForegroundColor Green
}

Write-Host ""
Write-Host "🎯 Deployment Mode: $(if ($useExistingFoundry) {'Using Existing Foundry Project ✅'} else {'Creating New Foundry Project 🆕'})" -ForegroundColor $(if ($useExistingFoundry) {'Green'} else {'Cyan'})

# Validate required fields based on deployment mode
if ($useExistingFoundry) {
    $requiredFields = @(
        'AZURE_SUBSCRIPTION_ID',
        'AZURE_RESOURCE_GROUP',
        'AZURE_LOCATION',
        'AZURE_AI_PROJECT_NAME',
        'AZURE_AI_PROJECT_ENDPOINT',
        'AZURE_OPENAI_ENDPOINT',
        'AZURE_OPENAI_API_KEY'
    )
} else {
    # New Foundry project - only infrastructure fields required
    $requiredFields = @(
        'AZURE_SUBSCRIPTION_ID',
        'AZURE_RESOURCE_GROUP',
        'AZURE_LOCATION'
    )
}

$missingFields = @()
foreach ($field in $requiredFields) {
    if (-not $config[$field]) {
        $missingFields += $field
    }
}

if ($missingFields.Count -gt 0) {
    Write-Host "❌ Missing required fields in .env:" -ForegroundColor Red
    $missingFields | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
    Write-Host ""
    exit 1
}

Write-Host "✅ Configuration loaded successfully" -ForegroundColor Green
Write-Host ""
Write-Host "📋 Deployment Configuration:" -ForegroundColor Cyan
Write-Host "   Subscription     : $($config['AZURE_SUBSCRIPTION_ID'])" -ForegroundColor White
Write-Host "   Resource Group   : $($config['AZURE_RESOURCE_GROUP'])" -ForegroundColor White
Write-Host "   Location         : $($config['AZURE_LOCATION'])" -ForegroundColor White
if ($useExistingFoundry) {
    Write-Host "   Foundry Project  : $($config['AZURE_AI_PROJECT_NAME']) (existing)" -ForegroundColor Green
} else {
    Write-Host "   Foundry Project  : Will be created during deployment" -ForegroundColor Yellow
}
Write-Host "   OpenAI Endpoint  : $($config['AZURE_OPENAI_ENDPOINT'])" -ForegroundColor White
Write-Host ""

# ============================================================================
# Step 2: Azure CLI Authentication
# ============================================================================
Write-Host "📋 Step 2: Checking Azure CLI..." -ForegroundColor Yellow
Write-Host ""

# Check Azure CLI
if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
    Write-Host "❌ Azure CLI not found!" -ForegroundColor Red
    Write-Host "   Install from: https://aka.ms/azure-cli" -ForegroundColor Yellow
    exit 1
}

# Check login status by attempting to show account (output suppressed)
az account show 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "🔐 Not logged in. Running: az login" -ForegroundColor Yellow
    az login
    if ($LASTEXITCODE -ne 0) {
        Write-Host "❌ Login failed" -ForegroundColor Red
        exit 1
    }
}

Write-Host "✅ Azure CLI authenticated" -ForegroundColor Green

# Set subscription
Write-Host "🔧 Setting subscription..." -ForegroundColor Yellow
az account set --subscription $config['AZURE_SUBSCRIPTION_ID']
if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ Failed to set subscription" -ForegroundColor Red
    exit 1
}

# Check/Create resource group
Write-Host "📦 Checking resource group..." -ForegroundColor Yellow
$rgExists = az group exists --name $config['AZURE_RESOURCE_GROUP']
if ($rgExists -eq 'false') {
    Write-Host "   Creating resource group: $($config['AZURE_RESOURCE_GROUP'])" -ForegroundColor Yellow
    az group create --name $config['AZURE_RESOURCE_GROUP'] --location $config['AZURE_LOCATION'] --output none
    if ($LASTEXITCODE -ne 0) {
        Write-Host "❌ Failed to create resource group" -ForegroundColor Red
        exit 1
    }
}

Write-Host "✅ Azure environment ready" -ForegroundColor Green
Write-Host ""

# ============================================================================
# Step 3: Deploy Infrastructure
# ============================================================================
Write-Host "════════════════════════════════════════════════════════════" -ForegroundColor Green
Write-Host "  📦 Step 3: Deploying Infrastructure" -ForegroundColor Green
Write-Host "════════════════════════════════════════════════════════════" -ForegroundColor Green
Write-Host ""
Write-Host "Deploying:" -ForegroundColor Cyan
Write-Host "  • Azure Container Registry (for Docker images)" -ForegroundColor White
Write-Host "  • Cosmos DB with Gremlin API (graph database)" -ForegroundColor White
Write-Host "  • Key Vault (secrets management)" -ForegroundColor White
Write-Host "  • Application Insights & Log Analytics (monitoring)" -ForegroundColor White
Write-Host ""
Write-Host "⏳ This will take 10-15 minutes..." -ForegroundColor Yellow
Write-Host ""

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$deploymentName = "indigo-kg-$timestamp"
$bicepTemplate = "$PSScriptRoot/main.bicep"

# Build parameter arguments based on deployment mode
if ($useExistingFoundry) {
    Write-Host "📦 Using existing Foundry parameters..." -ForegroundColor Cyan
    $paramArgs = @(
        "location=$($config['AZURE_LOCATION'])"
        "environmentName=dev"
        "projectName=indigo-kg"
        "useExistingFoundryProject=true"
        "existingAiProjectName=$($config['AZURE_AI_PROJECT_NAME'])"
        "existingAiProjectEndpoint=$($config['AZURE_AI_PROJECT_ENDPOINT'])"
        "existingOpenAiEndpoint=$($config['AZURE_OPENAI_ENDPOINT'])"
        "existingOpenAiKey=$($config['AZURE_OPENAI_API_KEY'])"
    )
} else {
    Write-Host "🆕 Configuring for new Foundry project creation..." -ForegroundColor Cyan
    $paramArgs = @(
        "location=$($config['AZURE_LOCATION'])"
        "environmentName=dev"
        "projectName=indigo-kg"
        "useExistingFoundryProject=false"
    )
}

try {
    $deployment = az deployment group create `
        --name $deploymentName `
        --resource-group $config['AZURE_RESOURCE_GROUP'] `
        --template-file $bicepTemplate `
        --parameters $paramArgs `
        --output json | ConvertFrom-Json
    
    if ($LASTEXITCODE -ne 0) {
        throw "Deployment command failed"
    }
    
    Write-Host ""
    Write-Host "✅ Infrastructure deployed successfully!" -ForegroundColor Green
    Write-Host ""
    
} catch {
    Write-Host ""
    Write-Host "❌ Infrastructure deployment failed!" -ForegroundColor Red
    Write-Host ""
    Write-Host "Error details:" -ForegroundColor Yellow
    Write-Host "  az deployment group show --name $deploymentName --resource-group $($config['AZURE_RESOURCE_GROUP']) --query properties.error" -ForegroundColor White
    Write-Host ""
    exit 1
}

# Save outputs
$outputs = $deployment.properties.outputs
$outputDir = "$PSScriptRoot/../.azure"
if (-not (Test-Path $outputDir)) {
    New-Item -ItemType Directory -Path $outputDir | Out-Null
}

$outputs | ConvertTo-Json -Depth 10 | Out-File "$outputDir/infrastructure-outputs.json"
Write-Host "💾 Outputs saved to: .azure/infrastructure-outputs.json" -ForegroundColor Cyan
Write-Host ""

# Update .env with infrastructure outputs
Write-Host "📝 Updating .env with infrastructure outputs..." -ForegroundColor Yellow
$infraUpdates = @{
    'AZURE_KEY_VAULT_NAME' = $outputs.keyVaultName.value
    'AZURE_KEY_VAULT_URI' = $outputs.keyVaultUri.value
    'AZURE_CONTAINER_REGISTRY' = $outputs.acrName.value
    'AZURE_CONTAINER_REGISTRY_ENDPOINT' = $outputs.acrLoginServer.value
    'COSMOS_ENDPOINT' = $outputs.cosmosEndpoint.value
    'COSMOS_DATABASE' = $outputs.cosmosDatabase.value
    'COSMOS_CONTAINER' = $outputs.cosmosGraph.value
}

Update-EnvFile -Path $envPath -Updates $infraUpdates
Write-Host "✅ .env updated with infrastructure details" -ForegroundColor Green
Write-Host ""

# ============================================================================
# Step 4: Deploy Neo4j (Optional)
# ============================================================================
if (-not $SkipNeo4j) {
    Write-Host "════════════════════════════════════════════════════════════" -ForegroundColor Green
    Write-Host "  🗄️  Step 4: Deploying Neo4j Database (Optional)" -ForegroundColor Green
    Write-Host "════════════════════════════════════════════════════════════" -ForegroundColor Green
    Write-Host ""
    Write-Host "Neo4j allows you to run your existing agents without code changes." -ForegroundColor Cyan
    Write-Host ""
    
    $deployNeo4j = $true
    if (-not $Neo4jPassword) {
        $response = Read-Host "Deploy Neo4j database? (Y/n)"
        if ($response -eq 'n' -or $response -eq 'N') {
            $deployNeo4j = $false
        }
    }
    
    if ($deployNeo4j) {
        # Get Neo4j password
        $neo4jPasswordPlain = ""
        if (-not $Neo4jPassword) {
            Write-Host ""
            $Neo4jPassword = Read-Host "Enter Neo4j password (min 8 characters)" -AsSecureString
        }
        
        # Convert SecureString to plain text for deployment
        $BSTR = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($Neo4jPassword)
        $neo4jPasswordPlain = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($BSTR)
        [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($BSTR)
        
        if ($neo4jPasswordPlain.Length -lt 8) {
            Write-Host "❌ Password must be at least 8 characters" -ForegroundColor Red
            Write-Host "⏭️  Skipping Neo4j deployment" -ForegroundColor Yellow
            $deployNeo4j = $false
        }
        
        if ($deployNeo4j) {
            Write-Host ""
            Write-Host "⏳ Deploying Neo4j (5-10 minutes)..." -ForegroundColor Yellow
            Write-Host ""
            
            $neo4jDeploymentName = "neo4j-$timestamp"
            $neo4jTemplate = "$PSScriptRoot/neo4j-aci.bicep"
            
            try {
                $neo4jDeployment = az deployment group create `
                    --name $neo4jDeploymentName `
                    --resource-group $config['AZURE_RESOURCE_GROUP'] `
                    --template-file $neo4jTemplate `
                    --parameters location=$($config['AZURE_LOCATION']) environmentName=dev projectName=indigo-kg neo4jPassword=$neo4jPasswordPlain `
                    --output json | ConvertFrom-Json
                
                if ($LASTEXITCODE -ne 0) {
                    throw "Neo4j deployment failed"
                }
                
                Write-Host ""
                Write-Host "✅ Neo4j deployed successfully!" -ForegroundColor Green
                Write-Host ""
                
                # Save Neo4j outputs
                $neo4jOutputs = $neo4jDeployment.properties.outputs
                $neo4jOutputs | ConvertTo-Json -Depth 10 | Out-File "$outputDir/neo4j-outputs.json"
                
                # Update .env with Neo4j details
                Write-Host "📝 Updating .env with Neo4j connection details..." -ForegroundColor Yellow
                $neo4jUpdates = @{
                    'NEO4J_URI' = $neo4jOutputs.neo4jBoltUrl.value
                    'NEO4J_USER' = $neo4jOutputs.neo4jUser.value
                    'NEO4J_PASSWORD' = $neo4jPasswordPlain
                }
                
                Update-EnvFile -Path $envPath -Updates $neo4jUpdates
                Write-Host "✅ .env updated with Neo4j details" -ForegroundColor Green
                Write-Host ""
                
                # Display Neo4j info
                Write-Host "📊 Neo4j Access Information:" -ForegroundColor Cyan
                Write-Host "   Browser URL: $($neo4jOutputs.neo4jBrowserUrl.value)" -ForegroundColor White
                Write-Host "   Bolt URL   : $($neo4jOutputs.neo4jBoltUrl.value)" -ForegroundColor White
                Write-Host "   Username   : neo4j" -ForegroundColor White
                Write-Host "   Password   : *** (saved in .env)" -ForegroundColor White
                Write-Host ""
                
            } catch {
                Write-Host ""
                Write-Host "❌ Neo4j deployment failed!" -ForegroundColor Red
                Write-Host "   You can deploy it later using: .\infra\deploy-neo4j.ps1" -ForegroundColor Yellow
                Write-Host ""
            }
        }
    } else {
        Write-Host "⏭️  Skipping Neo4j deployment" -ForegroundColor Yellow
        Write-Host "   You can deploy it later using: .\infra\deploy-neo4j.ps1" -ForegroundColor White
        Write-Host ""
    }
} else {
    Write-Host "⏭️  Neo4j deployment skipped (use -SkipNeo4j:$false to enable)" -ForegroundColor Yellow
    Write-Host ""
}

# ============================================================================
# Final Summary
# ============================================================================
Write-Host ""
Write-Host "╔═══════════════════════════════════════════════════════════╗" -ForegroundColor Green
Write-Host "║                                                           ║" -ForegroundColor Green
Write-Host "║           🎉 Deployment Complete! 🎉                      ║" -ForegroundColor Green
Write-Host "║                                                           ║" -ForegroundColor Green
Write-Host "╚═══════════════════════════════════════════════════════════╝" -ForegroundColor Green
Write-Host ""

Write-Host "📊 Deployed Resources:" -ForegroundColor Cyan
Write-Host ""
Write-Host "Using Existing Foundry Project:" -ForegroundColor Yellow
Write-Host "   Project Name    : $($config['AZURE_AI_PROJECT_NAME'])" -ForegroundColor White
Write-Host "   OpenAI Endpoint : $($config['AZURE_OPENAI_ENDPOINT'])" -ForegroundColor White
Write-Host ""
Write-Host "New Infrastructure Deployed:" -ForegroundColor Yellow
Write-Host "   Key Vault         : $($outputs.keyVaultName.value)" -ForegroundColor White
Write-Host "   Container Registry: $($outputs.acrName.value)" -ForegroundColor White
Write-Host "   Cosmos DB         : $($outputs.cosmosEndpoint.value)" -ForegroundColor White

if (-not $SkipNeo4j -and $neo4jOutputs) {
    Write-Host "   Neo4j Browser     : $($neo4jOutputs.neo4jBrowserUrl.value)" -ForegroundColor White
}
Write-Host ""

Write-Host "📚 Next Steps:" -ForegroundColor Cyan
Write-Host ""
Write-Host "1. Test Neo4j connection (if deployed):" -ForegroundColor Yellow
Write-Host "   python -c `"from src.utils.neo4j_helpers import get_neo4j_driver; d = get_neo4j_driver(); print('✅ Connected'); d.close()`"" -ForegroundColor White
Write-Host ""
Write-Host "2. Run your knowledge graph agents:" -ForegroundColor Yellow
Write-Host "   python -m src.agents.advanced_graph_builder_agent --database all" -ForegroundColor White
Write-Host ""
Write-Host "3. Unify graphs:" -ForegroundColor Yellow
Write-Host "   python -m src.graph_unification --graph1 output/CLMS_concept_graph.json --graph2 output/CrewPortal_concept_graph.json" -ForegroundColor White
Write-Host ""
Write-Host "4. (Optional) Build and push Docker images:" -ForegroundColor Yellow
Write-Host "   cd infra" -ForegroundColor White
Write-Host "   .\build-agents.ps1 -AcrName $($outputs.acrName.value)" -ForegroundColor White
Write-Host ""

Write-Host "📁 Important Files:" -ForegroundColor Cyan
Write-Host "   .env                              - Updated with all connection details" -ForegroundColor White
Write-Host "   .azure/infrastructure-outputs.json - Infrastructure deployment outputs" -ForegroundColor White
if (-not $SkipNeo4j -and $neo4jOutputs) {
    Write-Host "   .azure/neo4j-outputs.json         - Neo4j deployment outputs" -ForegroundColor White
}
Write-Host ""

Write-Host "═══════════════════════════════════════════════════════════" -ForegroundColor Green
Write-Host "  🚀 Your Indigo Knowledge Graph platform is ready!" -ForegroundColor Green
Write-Host "═══════════════════════════════════════════════════════════" -ForegroundColor Green
Write-Host ""
