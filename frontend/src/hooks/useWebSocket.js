import { useEffect, useMemo, useRef, useState } from "react";

import { getTask } from "../services/api";

const MAX_RECONNECT_ATTEMPTS = 3;
const RECONNECT_DELAY_MS = 3000;
const POLL_INTERVAL_MS = 3000;

export default function useWebSocket(taskId) {
  const [events, setEvents] = useState([]);
  const [isConnected, setIsConnected] = useState(false);
  const [error, setError] = useState(null);

  const socketRef = useRef(null);
  const reconnectAttemptsRef = useRef(0);
  const reconnectTimerRef = useRef(null);
  const pollingTimerRef = useRef(null);
  const pollingInFlightRef = useRef(false);

  const wsUrl = useMemo(() => {
    if (!taskId) {
      return null;
    }
    return `ws://localhost:8000/ws/${taskId}`;
  }, [taskId]);

  useEffect(() => {
    let cancelled = false;

    const appendEvent = (eventPayload) => {
      setEvents((prev) => [...prev, eventPayload]);
    };

    const clearReconnectTimer = () => {
      if (reconnectTimerRef.current) {
        window.clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
    };

    const clearPollingTimer = () => {
      if (pollingTimerRef.current) {
        window.clearInterval(pollingTimerRef.current);
        pollingTimerRef.current = null;
      }
    };

    const stopSocket = () => {
      if (socketRef.current) {
        try {
          socketRef.current.close();
        } catch {
          // noop
        }
        socketRef.current = null;
      }
    };

    const runPollingTick = async () => {
      if (!taskId || pollingInFlightRef.current || cancelled) {
        return;
      }

      pollingInFlightRef.current = true;
      try {
        const response = await getTask(taskId);
        if (!response.success) {
          setError(response.error || "Polling failed");
          return;
        }

        const state = response.data || null;
        appendEvent({
          event_type: "poll_state",
          timestamp: new Date().toISOString(),
          task_id: taskId,
          data: { state, source: "polling" },
        });

        const status = String(state?.status || "").toLowerCase();
        if (status === "completed" || status === "failed") {
          clearPollingTimer();
        }
      } catch (pollError) {
        setError(pollError?.message || "Polling failed");
      } finally {
        pollingInFlightRef.current = false;
      }
    };

    const startPolling = () => {
      if (!taskId || pollingTimerRef.current || cancelled) {
        return;
      }

      appendEvent({
        event_type: "polling_started",
        timestamp: new Date().toISOString(),
        task_id: taskId,
        data: { reason: "websocket_unavailable" },
      });

      runPollingTick();
      pollingTimerRef.current = window.setInterval(runPollingTick, POLL_INTERVAL_MS);
    };

    const scheduleReconnect = () => {
      if (cancelled) {
        return;
      }

      if (reconnectAttemptsRef.current >= MAX_RECONNECT_ATTEMPTS) {
        setError("WebSocket unavailable. Falling back to polling.");
        startPolling();
        return;
      }

      reconnectAttemptsRef.current += 1;
      const attempt = reconnectAttemptsRef.current;
      reconnectTimerRef.current = window.setTimeout(() => {
        appendEvent({
          event_type: "ws_reconnect_attempt",
          timestamp: new Date().toISOString(),
          task_id: taskId,
          data: { attempt },
        });
        connectWebSocket();
      }, RECONNECT_DELAY_MS);
    };

    const connectWebSocket = () => {
      if (!wsUrl || cancelled) {
        return;
      }

      clearReconnectTimer();

      try {
        const socket = new WebSocket(wsUrl);
        socketRef.current = socket;

        socket.onopen = () => {
          if (cancelled) {
            return;
          }
          setIsConnected(true);
          setError(null);
          reconnectAttemptsRef.current = 0;
          clearPollingTimer();
          appendEvent({
            event_type: "ws_connected",
            timestamp: new Date().toISOString(),
            task_id: taskId,
            data: {},
          });
        };

        socket.onmessage = (messageEvent) => {
          if (cancelled) {
            return;
          }
          try {
            const parsed = JSON.parse(messageEvent.data);
            appendEvent(parsed);
          } catch {
            appendEvent({
              event_type: "ws_raw_message",
              timestamp: new Date().toISOString(),
              task_id: taskId,
              data: { raw: String(messageEvent.data) },
            });
          }
        };

        socket.onerror = () => {
          if (cancelled) {
            return;
          }
          setError("WebSocket error");
        };

        socket.onclose = () => {
          if (cancelled) {
            return;
          }
          setIsConnected(false);
          scheduleReconnect();
        };
      } catch (connectionError) {
        setIsConnected(false);
        setError(connectionError?.message || "WebSocket initialization failed");
        scheduleReconnect();
      }
    };

    if (!taskId || !wsUrl) {
      setEvents([]);
      setIsConnected(false);
      setError(null);
      return () => undefined;
    }

    setEvents([]);
    setIsConnected(false);
    setError(null);
    reconnectAttemptsRef.current = 0;
    connectWebSocket();

    return () => {
      cancelled = true;
      clearReconnectTimer();
      clearPollingTimer();
      stopSocket();
      setIsConnected(false);
    };
  }, [taskId, wsUrl]);

  return { events, isConnected, error };
}
