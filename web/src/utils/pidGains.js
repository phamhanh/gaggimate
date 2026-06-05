export function parsePidTriplet(pidString) {
  if (!pidString) {
    return { kp: null, ki: null, kd: null };
  }

  const parts = String(pidString)
    .split(',')
    .map(part => part.trim());

  const toNum = value => {
    const num = Number(value);
    return Number.isFinite(num) ? num : null;
  };

  return {
    kp: toNum(parts[0]),
    ki: toNum(parts[1]),
    kd: toNum(parts[2]),
  };
}

export function getActiveZonePidGains(settings, zone) {
  if (!settings) {
    return null;
  }

  switch (zone) {
    case 1:
      return parsePidTriplet(settings.pidStab);
    case 2:
      return parsePidTriplet(settings.pidCool);
    case 0:
    default:
      return parsePidTriplet(settings.pid);
  }
}
