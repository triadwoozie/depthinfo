/**
 * useSonarSocket.ts — Reconnecting WebSocket hook for Sonar Imaging dashboard.
 *
 * Design rules (from AGENTS.md Rule 2):
 *  - High-frequency correlation_curve data is stored in a useRef, NOT useState,
 *    so it never triggers a React re-render (canvas draws via rAF instead).
 *  - Only low-frequency scalar values (depth, status, fps, etc.) are stored
 *    in useState so React components can subscribe to them efficiently.
 *  - Calibration updates are debounced briefly before being sent to the server.
 */

"use client"

import { useCallback, useEffect, useRef, useState } from "react"

import { API_BASE, WS_URL } from "@/lib/api"
import {
  CalibrationState,
  DEFAULT_OPERATOR_CONFIG,
  DEFAULT_CALIBRATION,
  OperatorConfig,
  SonarTelemetry,
  SystemConfig,
  WsIncomingMessage,
  operatorPatchToSystem,
  systemToCalibration,
  systemToOperator,
} from "@/types/sonar"

const RECONNECT_DELAY_MS = 2000
const CALIBRATION_DEBOUNCE_MS = 100
const CONFIG_DEBOUNCE_MS = 100

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface SonarSocketState {
  /** Whether the WebSocket is currently connected. */
  connected: boolean

  /** Last received scalar telemetry (excludes correlation_curve). */
  telemetry: Omit<SonarTelemetry, "correlation_curve"> | null

  /**
   * Ref containing the latest raw correlation_curve (256 floats).
   * Read this inside requestAnimationFrame for canvas rendering — do NOT
   * put it in state.
   */
  curveRef: React.MutableRefObject<number[]>

  /** Current calibration state as known by the dashboard. */
  calibration: CalibrationState
  operatorConfig: OperatorConfig
  configError: string | null

  /**
   * Send a partial calibration update to the backend.
   * Calls are automatically debounced by CALIBRATION_DEBOUNCE_MS.
   */
  updateCalibration: (patch: Partial<CalibrationState>) => void
  updateOperatorConfig: (patch: Partial<OperatorConfig>) => void

  /** Round-trip latency estimate in ms (based on timestamp delta). */
  pingMs: number | null
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useSonarSocket(): SonarSocketState {
  const [connected, setConnected] = useState(false)
  const [telemetry, setTelemetry] = useState<Omit<SonarTelemetry, "correlation_curve"> | null>(
    null
  )
  const [calibration, setCalibration] = useState<CalibrationState>(DEFAULT_CALIBRATION)
  const [operatorConfig, setOperatorConfig] = useState<OperatorConfig>(DEFAULT_OPERATOR_CONFIG)
  const [configError, setConfigError] = useState<string | null>(null)
  const [pingMs, setPingMs] = useState<number | null>(null)

  // High-frequency curve stored in a ref — never triggers renders
  const curveRef = useRef<number[]>(new Array(256).fill(0))

  // WebSocket ref (stable across renders)
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Calibration debounce
  const pendingCalibrationRef = useRef<Partial<CalibrationState>>({})
  const debounceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const pendingConfigRef = useRef<Partial<OperatorConfig>>({})
  const configTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const configTouchedRef = useRef(false)

  // Latest backend SystemConfig + the volume to restore when silent mode is switched off
  const systemRef = useRef<SystemConfig | null>(null)
  const lastVolumeRef = useRef(DEFAULT_OPERATOR_CONFIG.speaker_volume)

  // ---------------------------------------------------------------------------
  // Send calibration (debounced)
  // ---------------------------------------------------------------------------

  const flushCalibration = useCallback(() => {
    const ws = wsRef.current
    if (!ws || ws.readyState !== WebSocket.OPEN) return
    const patch = pendingCalibrationRef.current
    if (Object.keys(patch).length === 0) return
    ws.send(JSON.stringify(patch))
    pendingCalibrationRef.current = {}
  }, [])

  const flushOperatorConfig = useCallback(() => {
    const ws = wsRef.current
    if (!ws || ws.readyState !== WebSocket.OPEN) return
    const patch = pendingConfigRef.current
    if (Object.keys(patch).length === 0) return
    const systemPatch = operatorPatchToSystem(patch, systemRef.current, lastVolumeRef.current)
    pendingConfigRef.current = {}
    if (Object.keys(systemPatch).length === 0) return
    ws.send(JSON.stringify(systemPatch))
  }, [])

  const updateCalibration = useCallback(
    (patch: Partial<CalibrationState>) => {
      // Merge patch into local state immediately for responsive UI
      setCalibration((prev) => ({ ...prev, ...patch }))
      // Accumulate patch for debounced send
      pendingCalibrationRef.current = { ...pendingCalibrationRef.current, ...patch }
      if (debounceTimerRef.current) clearTimeout(debounceTimerRef.current)
      debounceTimerRef.current = setTimeout(flushCalibration, CALIBRATION_DEBOUNCE_MS)
    },
    [flushCalibration]
  )

  const updateOperatorConfig = useCallback(
    (patch: Partial<OperatorConfig>) => {
      configTouchedRef.current = true
      setOperatorConfig((prev) => ({
        ...prev,
        ...patch,
        ...(patch.rig
          ? {
              rig: {
                ...prev.rig,
                ...patch.rig,
              },
            }
          : {}),
      }))
      pendingConfigRef.current = {
        ...pendingConfigRef.current,
        ...patch,
        ...(patch.rig
          ? {
              rig: {
                ...pendingConfigRef.current.rig,
                ...patch.rig,
              },
            }
          : {}),
      }
      if (configTimerRef.current) clearTimeout(configTimerRef.current)
      configTimerRef.current = setTimeout(flushOperatorConfig, CONFIG_DEBOUNCE_MS)
    },
    [flushOperatorConfig]
  )

  /** Adopt a backend SystemConfig, keeping any edits that have not been flushed yet. */
  const applySystemConfig = useCallback((sys: SystemConfig) => {
    systemRef.current = sys
    if (sys.acoustics.speaker_volume > 0) lastVolumeRef.current = sys.acoustics.speaker_volume
    const fromServer = systemToOperator(sys, lastVolumeRef.current)
    const pending = pendingConfigRef.current
    setOperatorConfig({
      ...fromServer,
      ...pending,
      rig: { ...fromServer.rig, ...pending.rig },
    })
    setCalibration({ ...systemToCalibration(sys), ...pendingCalibrationRef.current })
    setConfigError(null)
  }, [])

  // ---------------------------------------------------------------------------
  // WebSocket lifecycle
  // ---------------------------------------------------------------------------

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return

    const ws = new WebSocket(WS_URL)
    wsRef.current = ws

    ws.onopen = () => {
      setConnected(true)
      flushCalibration()
      flushOperatorConfig()
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current)
        reconnectTimerRef.current = null
      }
    }

    ws.onclose = () => {
      setConnected(false)
      wsRef.current = null
      // Schedule reconnect
      reconnectTimerRef.current = setTimeout(connect, RECONNECT_DELAY_MS)
    }

    ws.onerror = () => {
      ws.close()
    }

    ws.onmessage = (event: MessageEvent<string>) => {
      let msg: WsIncomingMessage
      try {
        msg = JSON.parse(event.data)
      } catch {
        return
      }

      // Calibration acknowledgement from the server
      if ("type" in msg && msg.type === "calibration_ack") {
        setCalibration(msg.calibration)
        return
      }
      if ("type" in msg && msg.type === "config_update") {
        applySystemConfig(msg.config)
        return
      }
      if ("type" in msg && msg.type === "error") {
        setConfigError("Backend rejected a configuration value")
        return
      }

      // Telemetry frame
      const frame = msg as SonarTelemetry

      // Update high-frequency curve ref (no re-render)
      if (Array.isArray(frame.correlation_curve)) {
        curveRef.current = frame.correlation_curve
      }

      // Compute ping from timestamp
      const now = Date.now() / 1000
      setPingMs(Math.round((now - frame.timestamp) * 1000))

      // Destructure curve out so setState doesn't hold a large array
      const { correlation_curve: _curve, ...scalars } = frame
      setTelemetry(scalars)
    }
  }, [applySystemConfig, flushCalibration, flushOperatorConfig])

  useEffect(() => {
    let active = true
    fetch(`${API_BASE}/api/config`)
      .then(async (response) => {
        if (!response.ok) {
          throw new Error(`Config load failed (${response.status})`)
        }
        return (await response.json()) as SystemConfig
      })
      .then((config) => {
        if (active && !configTouchedRef.current) applySystemConfig(config)
      })
      .catch((error: unknown) => {
        if (active) {
          setConfigError(error instanceof Error ? error.message : "Config load failed")
        }
      })
    connect()
    return () => {
      active = false
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current)
      if (debounceTimerRef.current) clearTimeout(debounceTimerRef.current)
      if (configTimerRef.current) clearTimeout(configTimerRef.current)
      wsRef.current?.close()
    }
  }, [applySystemConfig, connect])

  return {
    connected,
    telemetry,
    curveRef,
    calibration,
    operatorConfig,
    configError,
    updateCalibration,
    updateOperatorConfig,
    pingMs,
  }
}
