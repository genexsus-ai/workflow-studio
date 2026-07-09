import { createContext, useContext } from 'react'

export type AttachPort = 'model' | 'memory' | 'tools'

/** A node's request to open the connect picker: from its output port
 * (no port) or from one of an agent's attachment ports. */
export interface ConnectRequest {
  nodeId: string
  port?: AttachPort
}

/** Lets canvas nodes ask the Canvas to open the connect-a-node picker
 * (the n8n "+" affordance on outputs and agent ports). */
export const ConnectPickerContext = createContext<(request: ConnectRequest) => void>(() => {})

export const useConnectPicker = () => useContext(ConnectPickerContext)

/** Component types each agent port accepts. */
export const PORT_TYPES: Record<AttachPort, string[]> = {
  model: ['model'],
  memory: ['memory'],
  tools: ['tool', 'mcp'],
}
