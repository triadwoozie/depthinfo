/**
 * useSonarSocket.ts — Reconnecting WebSocket hook for Sonar Imaging dashboard.
 *
 * Design rules (from AGENTS.md Rule 2):
 *  - High-frequency correlation_curve data is stored in a useRef, NOT useState,
 *    so it never triggers a React re-render (canvas draws via rAF instead).
 *  - Only low-frequency scalar values (depth, status, fps, etc.) are stored
 *    in useState so React components can subscribe to them efficiently.
 *  - Calibration updates are debounced (300 ms) before being sent to the server.
 */

"use client"

import { useCallback, useEffect, useRef, useState } from "react"

import {
  CalibrationState,
  DEFAULT_CALIBRATION,
  SonarTelemetry,
  WsIncomingMessage,
} from "@/types/sonar"

const WS_URL = "ws://localhost:8000/ws/telemetry"
const RECONNECT_DELAY_MS = 2000
const CALIBRATION_DEBOUNCE_MS = 300

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

  /**
   * Send a partial calibration update to the backend.
   * Calls are automatically debounced by CALIBRATION_DEBOUNCE_MS.
   */
  updateCalibration: (patch: Partial<CalibrationState>) => void

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
  const [pingMs, setPingMs] = useState<number | null>(null)

  // High-frequency curve stored in a ref — never triggers renders
  const curveRef = useRef<number[]>(new Array(256).fill(0))

  // WebSocket ref (stable across renders)
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Calibration debounce
  const pendingCalibrationRef = useRef<Partial<CalibrationState>>({})
  const debounceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

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

  // ---------------------------------------------------------------------------
  // WebSocket lifecycle
  // ---------------------------------------------------------------------------

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return

    const ws = new WebSocket(WS_URL)
    wsRef.current = ws

    ws.onopen = () => {
      setConnected(true)
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
  }, [])

  useEffect(() => {
    connect()
    return () => {
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current)
      if (debounceTimerRef.current) clearTimeout(debounceTimerRef.current)
      wsRef.current?.close()
    }
  }, [connect])

  return { connected, telemetry, curveRef, calibration, updateCalibration, pingMs }
}
