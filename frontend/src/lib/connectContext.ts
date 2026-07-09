import { createContext, useContext } from 'react'

/** Lets canvas nodes ask the Canvas to open the connect-a-node picker
 * anchored to their output port (the n8n "+" affordance). */
export const ConnectPickerContext = createContext<(sourceNodeId: string) => void>(() => {})

export const useConnectPicker = () => useContext(ConnectPickerContext)
