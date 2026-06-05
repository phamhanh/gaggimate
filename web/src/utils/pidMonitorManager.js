import { signal } from '@preact/signals';

export const PID_MONITOR_WINDOW_KEY = 'pidMonitorWindowMinutes';
export const DEFAULT_PID_MONITOR_WINDOW_MINUTES = 10;
export const MIN_PID_MONITOR_WINDOW_MINUTES = 1;
export const MAX_PID_MONITOR_WINDOW_MINUTES = 30;

/** History buffer cap: ~2 status ticks/s × max window. */
export const PID_MONITOR_MAX_HISTORY_POINTS =
  MAX_PID_MONITOR_WINDOW_MINUTES * 60 * 2;

function readWindowMinutes() {
  if (typeof window === 'undefined' || !window.localStorage) {
    return DEFAULT_PID_MONITOR_WINDOW_MINUTES;
  }

  try {
    const stored = Number(localStorage.getItem(PID_MONITOR_WINDOW_KEY));
    if (
      Number.isFinite(stored) &&
      stored >= MIN_PID_MONITOR_WINDOW_MINUTES &&
      stored <= MAX_PID_MONITOR_WINDOW_MINUTES
    ) {
      return stored;
    }
  } catch (error) {
    console.warn('getPidMonitorWindowMinutes: localStorage access failed:', error);
  }

  return DEFAULT_PID_MONITOR_WINDOW_MINUTES;
}

export const pidMonitorWindowMinutes = signal(readWindowMinutes());

export function getPidMonitorWindowMs() {
  return pidMonitorWindowMinutes.value * 60 * 1000;
}

export function setPidMonitorWindowMinutes(minutes) {
  const value = Math.min(
    MAX_PID_MONITOR_WINDOW_MINUTES,
    Math.max(MIN_PID_MONITOR_WINDOW_MINUTES, Math.round(Number(minutes))),
  );

  pidMonitorWindowMinutes.value = value;

  try {
    localStorage.setItem(PID_MONITOR_WINDOW_KEY, String(value));
  } catch (error) {
    console.warn('setPidMonitorWindowMinutes: localStorage write failed:', error);
  }

  return value;
}
