import type { ConnectorDbType } from '../types/api'

// The connection families that are not databases: files the user uploads, an SFTP drop, a REST or SOAP API.
export const CONNECTOR_TYPE_LABELS: Record<ConnectorDbType, string> = {
  file_upload: 'Uploaded files',
  sftp: 'SFTP',
  rest_api: 'REST API',
  soap_api: 'SOAP API',
}

export function isConnectorType(dbType: string | null): dbType is ConnectorDbType {
  return dbType === 'file_upload' || dbType === 'sftp' || dbType === 'rest_api' || dbType === 'soap_api'
}
