// Unified infrastructure template for Indigo Knowledge Graph
// Can either create new AI Foundry project OR use existing project
// Controlled by useExistingFoundryProject parameter

targetScope = 'resourceGroup'

@description('Primary location for all resources')
param location string = resourceGroup().location

@description('Environment name (dev, staging, prod)')
@allowed(['dev', 'staging', 'prod'])
param environmentName string = 'dev'

@description('Base name for all resources')
param projectName string = 'indigo-kg'

@description('Use existing Azure AI Foundry project instead of creating new one')
param useExistingFoundryProject bool = false

@description('Existing AI Project name (required if useExistingFoundryProject = true)')
param existingAiProjectName string = ''

@description('Existing AI Project endpoint (required if useExistingFoundryProject = true)')
param existingAiProjectEndpoint string = ''

@description('Existing Azure OpenAI endpoint (optional)')
param existingOpenAiEndpoint string = ''

@description('Existing Azure OpenAI key (optional)')
@secure()
param existingOpenAiKey string = ''

@description('AI Hub/Project name (used only if creating new Foundry project)')
param aiProjectName string = '${projectName}-ai-${environmentName}'

@description('Cosmos DB account name')
param cosmosDbAccountName string = '${projectName}-cosmos-${environmentName}-${uniqueString(resourceGroup().id)}'

@description('Cosmos DB database name')
param cosmosDatabaseName string = 'knowledge_graph'

@description('Container Registry name')
param acrName string = replace('${projectName}acr${environmentName}${uniqueString(resourceGroup().id)}', '-', '')

@description('Log Analytics workspace name')
param logAnalyticsName string = '${projectName}-logs-${environmentName}'

@description('Application Insights name')
param appInsightsName string = '${projectName}-insights-${environmentName}'

@description('Key Vault name')
param keyVaultName string = 'kv${replace(projectName, '-', '')}${take(uniqueString(resourceGroup().id), 10)}'

// Common tags for all resources
var tags = {
  Environment: environmentName
  Project: projectName
  ManagedBy: 'Bicep'
  DeploymentMode: useExistingFoundryProject ? 'ExistingFoundry' : 'NewFoundry'
}

// ============================================================================
// Log Analytics Workspace
// ============================================================================
resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: logAnalyticsName
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
    features: {
      enableLogAccessUsingOnlyResourcePermissions: true
    }
  }
}

// ============================================================================
// Application Insights
// ============================================================================
resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalytics.id
    IngestionMode: 'LogAnalytics'
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}

// ============================================================================
// Key Vault
// ============================================================================
resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  tags: tags
  properties: {
    sku: {
      family: 'A'
      name: 'standard'
    }
    tenantId: subscription().tenantId
    enabledForDeployment: false
    enabledForDiskEncryption: false
    enabledForTemplateDeployment: true
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      bypass: 'AzureServices'
      defaultAction: 'Allow'
    }
  }
}

// ============================================================================
// Azure Container Registry
// ============================================================================
resource acr 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' = {
  name: acrName
  location: location
  tags: tags
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: true
    publicNetworkAccess: 'Enabled'
    zoneRedundancy: 'Disabled'
  }
}

// ============================================================================
// Azure Cosmos DB Account (Gremlin API for Graph Database)
// ============================================================================
resource cosmosDbAccount 'Microsoft.DocumentDB/databaseAccounts@2024-05-15' = {
  name: cosmosDbAccountName
  location: location
  tags: tags
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    consistencyPolicy: {
      defaultConsistencyLevel: 'Session'
    }
    locations: [
      {
        locationName: location
        failoverPriority: 0
        isZoneRedundant: false
      }
    ]
    capabilities: [
      {
        name: 'EnableGremlin'
      }
    ]
    enableFreeTier: false
    publicNetworkAccess: 'Enabled'
    enableAutomaticFailover: false
    enableMultipleWriteLocations: false
    disableLocalAuth: false
  }
}

// Cosmos DB Gremlin Database
resource cosmosDatabase 'Microsoft.DocumentDB/databaseAccounts/gremlinDatabases@2024-05-15' = {
  parent: cosmosDbAccount
  name: cosmosDatabaseName
  properties: {
    resource: {
      id: cosmosDatabaseName
    }
    options: {
      throughput: 400 // Minimum for manual throughput
    }
  }
}

// Cosmos DB Gremlin Graph (Container)
resource cosmosGraph 'Microsoft.DocumentDB/databaseAccounts/gremlinDatabases/graphs@2024-05-15' = {
  parent: cosmosDatabase
  name: 'concept_graph'
  properties: {
    resource: {
      id: 'concept_graph'
      partitionKey: {
        paths: [
          '/database'
        ]
        kind: 'Hash'
      }
      indexingPolicy: {
        indexingMode: 'consistent'
        automatic: true
        includedPaths: [
          {
            path: '/*'
          }
        ]
      }
    }
  }
}

// Store Cosmos DB connection secrets in Key Vault
resource cosmosConnectionStringSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'cosmos-connection-string'
  properties: {
    value: cosmosDbAccount.listConnectionStrings().connectionStrings[0].connectionString
  }
}

resource cosmosEndpointSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'cosmos-endpoint'
  properties: {
    value: cosmosDbAccount.properties.documentEndpoint
  }
}

resource cosmosKeySecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'cosmos-key'
  properties: {
    value: cosmosDbAccount.listKeys().primaryMasterKey
  }
}

// ============================================================================
// CONDITIONAL: Azure AI Services (only if creating new Foundry project)
// ============================================================================
resource aiServices 'Microsoft.CognitiveServices/accounts@2024-10-01' = if (!useExistingFoundryProject) {
  name: '${projectName}-ai-services-${environmentName}'
  location: location
  tags: tags
  kind: 'AIServices'
  sku: {
    name: 'S0'
  }
  properties: {
    customSubDomainName: '${projectName}-ai-services-${environmentName}-${uniqueString(resourceGroup().id)}'
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      defaultAction: 'Allow'
    }
  }
}

// Store AI Services secrets (only if creating new)
resource aiServicesEndpointSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = if (!useExistingFoundryProject) {
  parent: keyVault
  name: 'ai-services-endpoint'
  properties: {
    value: aiServices.properties.endpoint
  }
}

resource aiServicesKeySecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = if (!useExistingFoundryProject) {
  parent: keyVault
  name: 'ai-services-key'
  properties: {
    value: aiServices.listKeys().key1
  }
}

// ============================================================================
// CONDITIONAL: Azure AI Hub (only if creating new Foundry project)
// ============================================================================
resource aiHub 'Microsoft.MachineLearningServices/workspaces@2024-07-01-preview' = if (!useExistingFoundryProject) {
  name: '${aiProjectName}-hub'
  location: location
  tags: tags
  kind: 'Hub'
  sku: {
    name: 'Basic'
    tier: 'Basic'
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    friendlyName: 'Indigo KG AI Hub'
    description: 'AI Hub for Indigo Knowledge Graph agents'
    applicationInsights: appInsights.id
    keyVault: keyVault.id
    containerRegistry: acr.id
    publicNetworkAccess: 'Enabled'
  }
}

// ============================================================================
// CONDITIONAL: Azure AI Project (only if creating new Foundry project)
// ============================================================================
resource aiProject 'Microsoft.MachineLearningServices/workspaces@2024-07-01-preview' = if (!useExistingFoundryProject) {
  name: aiProjectName
  location: location
  tags: tags
  kind: 'Project'
  sku: {
    name: 'Basic'
    tier: 'Basic'
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    friendlyName: 'Indigo Knowledge Graph Project'
    description: 'AI Foundry project for knowledge graph extraction agents'
    hubResourceId: aiHub.id
    publicNetworkAccess: 'Enabled'
  }
}

// ============================================================================
// CONDITIONAL: RBAC Role Assignments (only if creating new Foundry project)
// ============================================================================

// Grant AI Hub managed identity access to Key Vault
resource aiHubKeyVaultAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!useExistingFoundryProject) {
  name: guid(keyVault.id, aiHub.id, 'Key Vault Secrets User')
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6') // Key Vault Secrets User
    principalId: aiHub.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// Grant AI Hub managed identity access to ACR
resource aiHubAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!useExistingFoundryProject) {
  name: guid(acr.id, aiHub.id, 'AcrPull')
  scope: acr
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d') // AcrPull
    principalId: aiHub.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// Grant AI Hub managed identity access to Cosmos DB
resource aiHubCosmosAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!useExistingFoundryProject) {
  name: guid(cosmosDbAccount.id, aiHub.id, 'Cosmos DB Account Reader')
  scope: cosmosDbAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'fbdf93bf-df7d-467e-a4d2-9458aa1360c8') // Cosmos DB Account Reader
    principalId: aiHub.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// Grant AI Hub managed identity access to AI Services
resource aiHubCognitiveAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!useExistingFoundryProject) {
  name: guid(aiServices.id, aiHub.id, 'Cognitive Services User')
  scope: aiServices
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'a97b65f3-24c7-4388-baec-2e87135dc908') // Cognitive Services User
    principalId: aiHub.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// ============================================================================
// Outputs
// ============================================================================

// AI Project outputs (conditional based on deployment mode)
output aiProjectName string = useExistingFoundryProject ? existingAiProjectName : aiProject.name
output aiProjectEndpoint string = useExistingFoundryProject ? existingAiProjectEndpoint : aiProject.properties.discoveryUrl
output aiHubName string = useExistingFoundryProject ? 'Using existing project' : aiHub.name

// AI Services outputs (conditional)
output aiServicesEndpoint string = useExistingFoundryProject ? existingOpenAiEndpoint : aiServices.properties.endpoint

// Always deployed resources
output keyVaultName string = keyVault.name
output keyVaultUri string = keyVault.properties.vaultUri
output acrName string = acr.name
output acrLoginServer string = acr.properties.loginServer
output cosmosEndpoint string = cosmosDbAccount.properties.documentEndpoint
output cosmosDatabase string = cosmosDatabaseName
output cosmosGraph string = 'concept_graph'
output appInsightsConnectionString string = appInsights.properties.ConnectionString
output logAnalyticsWorkspaceId string = logAnalytics.id

// Deployment mode indicator
output deploymentMode string = useExistingFoundryProject ? 'ExistingFoundry' : 'NewFoundry'
output useExistingFoundry bool = useExistingFoundryProject
