import { createContext, useContext } from 'react'

export type AttachPort = 'model' | 'memory' | 'tools' | 'agents'

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

/** Lets a flow edge ask the Canvas to open the picker to insert a node
 * in the middle of that edge (the Zapier/n8n "+" between two nodes). */
export const InsertPickerContext = createContext<(edgeId: string) => void>(() => {})

export const useInsertPicker = () => useContext(InsertPickerContext)

/** Component types each agent port accepts. */
export const PORT_TYPES: Record<AttachPort, string[]> = {
  model: ['model'],
  memory: ['memory'],
  tools: ['tool', 'mcp'],
  agents: ['agent'],
}
