// Alternative: Deploy Neo4j in Azure Container Instances
// Use this if you want to keep using Neo4j instead of migrating to Cosmos DB Gremlin API

targetScope = 'resourceGroup'

@description('Location for all resources')
param location string = resourceGroup().location

@description('Environment name')
param environmentName string = 'dev'

@description('Base name for resources')
param projectName string = 'indigo-kg'

@description('Neo4j container image (use official Neo4j image)')
param neo4jImage string = 'neo4j:5-community'

@description('Neo4j password (minimum 8 characters)')
@secure()
param neo4jPassword string

@description('Neo4j memory in GB')
param memoryInGb int = 4

@description('Neo4j CPU cores')
param cpuCores int = 2

var containerGroupName = '${projectName}-neo4j-${environmentName}'
var neo4jUser = 'neo4j'

// Container Instance for Neo4j
resource neo4jContainer 'Microsoft.ContainerInstance/containerGroups@2023-05-01' = {
  name: containerGroupName
  location: location
  properties: {
    containers: [
      {
        name: 'neo4j'
        properties: {
          image: neo4jImage
          resources: {
            requests: {
              cpu: cpuCores
              memoryInGB: memoryInGb
            }
          }
          ports: [
            {
              port: 7474
              protocol: 'TCP'
            }
            {
              port: 7687
              protocol: 'TCP'
            }
          ]
          environmentVariables: [
            {
              name: 'NEO4J_AUTH'
              value: '${neo4jUser}/${neo4jPassword}'
            }
            {
              name: 'NEO4J_ACCEPT_LICENSE_AGREEMENT'
              value: 'yes'
            }
            {
              name: 'NEO4J_server_memory_heap_initial__size'
              value: '2G'
            }
            {
              name: 'NEO4J_server_memory_heap_max__size'
              value: '2G'
            }
          ]
          volumeMounts: [
            {
              name: 'neo4j-data'
              mountPath: '/data'
            }
            {
              name: 'neo4j-logs'
              mountPath: '/logs'
            }
          ]
        }
      }
    ]
    osType: 'Linux'
    restartPolicy: 'Always'
    ipAddress: {
      type: 'Public'
      ports: [
        {
          port: 7474
          protocol: 'TCP'
        }
        {
          port: 7687
          protocol: 'TCP'
        }
      ]
      dnsNameLabel: '${projectName}-neo4j-${environmentName}-${uniqueString(resourceGroup().id)}'
    }
    volumes: [
      {
        name: 'neo4j-data'
        azureFile: {
          shareName: 'neo4j-data'
          storageAccountName: storageAccount.name
          storageAccountKey: storageAccount.listKeys().keys[0].value
        }
      }
      {
        name: 'neo4j-logs'
        azureFile: {
          shareName: 'neo4j-logs'
          storageAccountName: storageAccount.name
          storageAccountKey: storageAccount.listKeys().keys[0].value
        }
      }
    ]
  }
}

// Storage Account for Neo4j data persistence
resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: '${replace(projectName, '-', '')}neo4j${environmentName}${uniqueString(resourceGroup().id)}'
  location: location
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    accessTier: 'Hot'
  }
}

// File shares for Neo4j volumes
resource fileServices 'Microsoft.Storage/storageAccounts/fileServices@2023-05-01' = {
  parent: storageAccount
  name: 'default'
}

resource dataFileShare 'Microsoft.Storage/storageAccounts/fileServices/shares@2023-05-01' = {
  parent: fileServices
  name: 'neo4j-data'
  properties: {
    shareQuota: 100
  }
}

resource logsFileShare 'Microsoft.Storage/storageAccounts/fileServices/shares@2023-05-01' = {
  parent: fileServices
  name: 'neo4j-logs'
  properties: {
    shareQuota: 10
  }
}

// Outputs
output neo4jBrowserUrl string = 'http://${neo4jContainer.properties.ipAddress.fqdn}:7474'
output neo4jBoltUrl string = 'bolt://${neo4jContainer.properties.ipAddress.fqdn}:7687'
output neo4jFqdn string = neo4jContainer.properties.ipAddress.fqdn
output neo4jIpAddress string = neo4jContainer.properties.ipAddress.ip
output neo4jUser string = neo4jUser
output storageAccountName string = storageAccount.name
