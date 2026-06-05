import { machine } from '../services/ApiService.js';
import { getActiveZonePidGains } from '../utils/pidGains.js';

function fmt(value, digits = 2) {
  if (value == null || Number.isNaN(Number(value))) {
    return '—';
  }
  return Number(value).toFixed(digits);
}

function modeLabel(mode) {
  switch (mode) {
    case 0:
      return 'Standby';
    case 1:
      return 'Brew';
    case 2:
      return 'Steam';
    case 3:
      return 'Water';
    case 4:
      return 'Grind';
    default:
      return String(mode ?? '—');
  }
}

function StatRow({ label, value, badge }) {
  return (
    <div className='flex items-baseline justify-between gap-2 py-1'>
      <span className='text-base-content/70 text-sm'>{label}</span>
      <span className='font-mono text-sm font-medium tabular-nums'>
        {badge ? (
          <span className={`badge badge-sm ${badge === 'Frozen' ? 'badge-warning' : 'badge-success'}`}>
            {badge}
          </span>
        ) : (
          value
        )}
      </span>
    </div>
  );
}

function StatGroup({ title, children }) {
  return (
    <div>
      <h3 className='text-base-content/60 mb-1 text-xs font-semibold tracking-wide uppercase'>
        {title}
      </h3>
      <div className='divide-base-300 divide-y'>{children}</div>
    </div>
  );
}

function zoneLabel(zone) {
  switch (zone) {
    case 0:
      return 'Heating';
    case 1:
      return 'Stabilizing';
    case 2:
      return 'Cooling';
    default:
      return 'Heating';
  }
}

export function PidStatusPanel({ pidSettings = null }) {
  const status = machine.value.status;
  const pid = status.pidLive || {};
  const frozen = pid.frozen === 1;
  const zone = pid.zone ?? 0;
  const activeGains = getActiveZonePidGains(pidSettings, zone);

  return (
    <div className='flex flex-col gap-4'>
      <StatGroup title='Temperature'>
        <StatRow label='CT' value={`${fmt(status.currentTemperature, 1)} °C`} />
        <StatRow label='TT' value={`${fmt(status.targetTemperature, 1)} °C`} />
      </StatGroup>

      <StatGroup title='Stored PID'>
        <StatRow label='Zone' badge={zoneLabel(zone)} />
        <StatRow label='Kp' value={fmt(activeGains?.kp ?? status.kp, 3)} />
        <StatRow label='Ki' value={fmt(activeGains?.ki ?? status.ki, 3)} />
        <StatRow label='Kd' value={fmt(activeGains?.kd ?? status.kd, 3)} />
        <StatRow label='Kff gain' value={fmt(status.kffGain, 3)} />
      </StatGroup>

      <StatGroup title='Power'>
        <StatRow label='Pump' value={`${fmt(status.pumpPower, 1)} %`} />
        <StatRow label='Total' value={fmt(status.totalPower, 1)} />
      </StatGroup>

      <StatGroup title='Live PID'>
        <StatRow label='State' badge={frozen ? 'Frozen' : zoneLabel(zone)} />
        <StatRow label='P' value={fmt(pid.p, 2)} />
        <StatRow label='I' value={fmt(pid.i, 2)} />
        <StatRow label='D' value={fmt(pid.d, 2)} />
        <StatRow label='Kff out' value={fmt(pid.kff, 2)} />
        <StatRow label='Out' value={fmt(pid.out ?? status.heaterPower, 1)} />
      </StatGroup>

      <StatGroup title='Context'>
        <StatRow label='Mode' value={modeLabel(status.mode)} />
        <StatRow label='Pressure' value={`${fmt(status.currentPressure, 2)} bar`} />
        <StatRow label='Flow' value={`${fmt(status.currentFlow, 2)} g/s`} />
        <StatRow label='Weight' value={`${fmt(status.currentWeight, 1)} g`} />
      </StatGroup>
    </div>
  );
}
