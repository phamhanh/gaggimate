import { faFileExport } from '@fortawesome/free-solid-svg-icons/faFileExport';
import { faFileImport } from '@fortawesome/free-solid-svg-icons/faFileImport';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { computed } from '@preact/signals';
import { useQuery } from 'preact-fetching';
import { useCallback, useEffect, useRef, useState } from 'preact/hooks';
import Card from '../../components/Card.jsx';
import { Spinner } from '../../components/Spinner.jsx';
import { timezones } from '../../config/zones.js';
import { machine } from '../../services/ApiService.js';
import { DASHBOARD_LAYOUTS, setDashboardLayout } from '../../utils/dashboardManager.js';
import { downloadJson } from '../../utils/download.js';
import { getStoredTheme, handleThemeChange } from '../../utils/themeManager.js';
import { PluginCard } from './PluginCard.jsx';
import { faEye } from '@fortawesome/free-solid-svg-icons/faEye';
import { faEyeSlash } from '@fortawesome/free-solid-svg-icons/faEyeSlash';
import { Tooltip } from '../../components/Tooltip.jsx';
import { faRefresh } from '@fortawesome/free-solid-svg-icons/faRefresh';
import { faCrosshairs } from '@fortawesome/free-solid-svg-icons/faCrosshairs';

const ledControl = computed(() => machine.value.capabilities.ledControl);
const pressureAvailable = computed(() => machine.value.capabilities.pressure);
const dimmingAvailable = computed(() => machine.value.capabilities.dimming);
const showPumpFlowCoeffs = computed(() => pressureAvailable.value);
const showPumpConfig = computed(() => showPumpFlowCoeffs.value);
const tofDistance = computed(() => machine.value.status.tofDistance);

/**
 * Split a PID CSV string into the form's two-input shape.
 *
 * The firmware stores PID as a single CSV `Kp,Ki,Kd,Kff` string, but the
 * form edits Kp/Ki/Kd as one input and Kff as another. This converts the
 * on-wire shape into `{ pid, kf }` for the form. Used both on initial
 * fetch and after every Save — without re-splitting on the post-save
 * response, a fourth field leaks into the `pid` input and the next Save
 * sends a 5-field CSV.
 *
 * @param {string|undefined} pidString - CSV `Kp,Ki,Kd,Kff` string from the
 *   firmware, or empty/undefined if no PID has been saved yet.
 * @returns {{ pid: string, kf: string }} - `pid` is the first three CSV
 *   fields joined by commas; `kf` is the fourth field, or `'0.000'` if
 *   absent.
 */
function splitPidString(pidString) {
  if (!pidString) return { pid: pidString, kf: '0.000' };
  const parts = pidString.split(',');
  if (parts.length >= 4) {
    return { pid: parts.slice(0, 3).join(','), kf: parts[3] };
  }
  return { pid: pidString, kf: '0.000' };
}

function splitPumpModelCoeffs(coeffs) {
  if (!coeffs) {
    return { pumpFlow1Bar: '10.205', pumpFlow9Bar: '5.521' };
  }
  const parts = coeffs.split(',');
  return {
    pumpFlow1Bar: parts[0] ?? '',
    pumpFlow9Bar: parts[1] ?? '',
  };
}

function getWifiStatusLabel(formData) {
  if (formData.wifiApFallback) {
    return 'AP fallback';
  }
  if (formData.wifiConnected) {
    return 'Connected';
  }
  return 'Disconnected';
}

export function Settings() {
  const [submitting, setSubmitting] = useState(false);
  const [gen] = useState(0);
  const [formData, setFormData] = useState({});
  const [currentTheme, setCurrentTheme] = useState('light');
  const [showWifiPassword, setShowWifiPassword] = useState(false);
  const [autowakeupSchedules, setAutoWakeupSchedules] = useState([
    { time: '07:00', endTime: '08:00', days: [true, true, true, true, true, true, true] },
  ]);
  const { isLoading, data: fetchedSettings } = useQuery(`settings/${gen}`, async () => {
    const response = await fetch(`/api/settings`);
    const data = await response.json();
    return data;
  });

  const formRef = useRef();

  useEffect(() => {
    if (fetchedSettings) {
      // Initialize standbyDisplayEnabled based on standby brightness value
      // but preserve it if it already exists in the fetched data
      const settingsWithToggle = {
        ...fetchedSettings,
        standbyDisplayEnabled:
          fetchedSettings.standbyDisplayEnabled !== undefined
            ? fetchedSettings.standbyDisplayEnabled
            : fetchedSettings.standbyBrightness > 0,
        dashboardLayout: fetchedSettings.dashboardLayout || DASHBOARD_LAYOUTS.ORDER_FIRST,
      };

      // Extract Kf from PID string and separate them. Mirrors the same
      // split applied in `onSubmit` after every save — keep these two in
      // sync via `splitPidString`.
      if (fetchedSettings.pid) {
        const split = splitPidString(fetchedSettings.pid);
        settingsWithToggle.pid = split.pid;
        settingsWithToggle.kf = split.kf;
      }

      const graceMs = fetchedSettings.pidFreezeGraceMs ?? 60000;
      settingsWithToggle.pidFreezeGraceSec = Math.round(graceMs / 1000);
      settingsWithToggle.incomingWaterTempC = fetchedSettings.incomingWaterTempC ?? 23;
      settingsWithToggle.tempProbeFilterEnabled = fetchedSettings.tempProbeFilterEnabled ?? true;
      settingsWithToggle.tempProbeFilterAlpha = fetchedSettings.tempProbeFilterAlpha ?? 0.05;
      settingsWithToggle.pidErrorAttenC = fetchedSettings.pidErrorAttenC ?? 0;
      settingsWithToggle.pidPdMuteEnabled = fetchedSettings.pidPdMuteEnabled ?? false;
      settingsWithToggle.pidPdMuteAboveC = fetchedSettings.pidPdMuteAboveC ?? 0.5;
      settingsWithToggle.pidKiAbove = fetchedSettings.pidKiAbove ?? settingsWithToggle.ki ?? 0.27;
      const stableMs = fetchedSettings.stableDurationMs ?? 8000;
      settingsWithToggle.stableDurationSec = Math.round(stableMs / 1000);

      const pumpSplit = splitPumpModelCoeffs(fetchedSettings.pumpModelCoeffs);
      settingsWithToggle.pumpFlow1Bar = pumpSplit.pumpFlow1Bar;
      settingsWithToggle.pumpFlow9Bar = pumpSplit.pumpFlow9Bar;
      if (fetchedSettings.autowakeupSchedules) {
        const schedules = [];
        if (
          typeof fetchedSettings.autowakeupSchedules === 'string' &&
          fetchedSettings.autowakeupSchedules.trim()
        ) {
          const scheduleStrings = fetchedSettings.autowakeupSchedules.split(';');
          for (const scheduleStr of scheduleStrings) {
            const parts = scheduleStr.split('|');
            if (parts.length === 3 && parts[2].length === 7) {
              schedules.push({
                time: parts[0],
                endTime: parts[1],
                days: parts[2].split('').map(d => d === '1'),
              });
            } else if (parts.length === 2 && parts[1].length === 7) {
              schedules.push({
                time: parts[0],
                days: parts[1].split('').map(d => d === '1'),
              });
            }
          }
        }
        if (schedules.length === 0) {
          schedules.push({
            time: '07:00',
            endTime: '08:00',
            days: [true, true, true, true, true, true, true],
          });
        }
        setAutoWakeupSchedules(schedules);
      } else {
        setAutoWakeupSchedules([
          { time: '07:00', endTime: '08:00', days: [true, true, true, true, true, true, true] },
        ]);
      }

      setFormData(settingsWithToggle);
    } else {
      setFormData({});
      setAutoWakeupSchedules([
        { time: '07:00', endTime: '08:00', days: [true, true, true, true, true, true, true] },
      ]);
    }
  }, [fetchedSettings]);

  // Initialize theme
  useEffect(() => {
    setCurrentTheme(getStoredTheme());
  }, []);

  const onChange = key => {
    return e => {
      let value = e.currentTarget.value;
      if (key === 'homekit') {
        value = !formData.homekit;
      }
      if (key === 'boilerFillActive') {
        value = !formData.boilerFillActive;
      }
      if (key === 'smartGrindActive') {
        value = !formData.smartGrindActive;
      }
      if (key === 'smartGrindToggle') {
        value = !formData.smartGrindToggle;
      }
      if (key === 'homeAssistant') {
        value = !formData.homeAssistant;
      }
      if (key === 'momentaryButtons') {
        value = !formData.momentaryButtons;
      }
      if (key === 'delayAdjust') {
        value = !formData.delayAdjust;
      }
      if (key === 'clock24hFormat') {
        value = !formData.clock24hFormat;
      }
      if (key === 'autowakeupEnabled') {
        value = !formData.autowakeupEnabled;
      }
      if (key === 'kffEnabled') {
        value = formData.kffEnabled === false;
      }
      if (key === 'tempProbeFilterEnabled') {
        value = formData.tempProbeFilterEnabled === false;
      }
      if (key === 'pidFreezeEnabled') {
        value = formData.pidFreezeEnabled === false;
      }
      if (key === 'pidGraceEnabled') {
        value = formData.pidGraceEnabled === false;
      }
      if (key === 'ventEnabled') {
        value = formData.ventEnabled === false;
      }
      if (key === 'standbyDisplayEnabled') {
        value = !formData.standbyDisplayEnabled;
        // Set standby brightness to 0 when toggle is off
        const newFormData = {
          ...formData,
          [key]: value,
        };
        if (!value) {
          newFormData.standbyBrightness = 0;
        }
        setFormData(newFormData);
        return;
      }
      if (key === 'dashboardLayout') {
        setDashboardLayout(value);
      }
      setFormData({
        ...formData,
        [key]: value,
      });
    };
  };

  const addAutoWakeupSchedule = () => {
    setAutoWakeupSchedules([
      ...autowakeupSchedules,
      {
        time: '07:00',
        endTime: '08:00',
        days: [true, true, true, true, true, true, true],
      },
    ]);
  };

  const removeAutoWakeupSchedule = index => {
    if (autowakeupSchedules.length > 1) {
      const newSchedules = autowakeupSchedules.filter((_, i) => i !== index);
      setAutoWakeupSchedules(newSchedules);
    }
  };

  const updateAutoWakeupTime = (index, value) => {
    const newSchedules = [...autowakeupSchedules];
    newSchedules[index].time = value;
    setAutoWakeupSchedules(newSchedules);
  };

  const updateAutoWakeupEndTime = (index, value) => {
    const newSchedules = [...autowakeupSchedules];
    newSchedules[index].endTime = value;
    setAutoWakeupSchedules(newSchedules);
  };

  const updateAutoWakeupDay = (scheduleIndex, dayIndex, enabled) => {
    const newSchedules = [...autowakeupSchedules];
    newSchedules[scheduleIndex].days[dayIndex] = enabled;
    setAutoWakeupSchedules(newSchedules);
  };

  const onSubmit = useCallback(
    async (e, restart = false) => {
      e.preventDefault();
      setSubmitting(true);
      const form = formRef.current;
      const formDataToSubmit = new FormData(form);
      formDataToSubmit.set('steamPumpPercentage', formData.steamPumpPercentage);
      formDataToSubmit.set(
        'altRelayFunction',
        formData.altRelayFunction !== undefined ? formData.altRelayFunction : 1,
      );

      // Combine PID and Kf into single PID string
      if (formData.pid && formData.kf !== undefined) {
        const combinedPid = `${formData.pid},${formData.kf}`;
        formDataToSubmit.set('pid', combinedPid);
      }

      const schedulesStr = autowakeupSchedules
        .map(schedule => {
          const days = schedule.days.map(d => (d ? '1' : '0')).join('');
          if (schedule.endTime) {
            return `${schedule.time}|${schedule.endTime}|${days}`;
          }
          return `${schedule.time}|${days}`;
        })
        .join(';');
      formDataToSubmit.set('autowakeupSchedules', schedulesStr);

      const graceSec = Number(formData.pidFreezeGraceSec ?? 60);
      formDataToSubmit.set('pidFreezeGraceMs', String(Math.max(0, Math.round(graceSec * 1000))));
      if (formData.incomingWaterTempC !== undefined) {
        const inlet = Math.min(80, Math.max(5, Math.round(Number(formData.incomingWaterTempC))));
        formDataToSubmit.set('incomingWaterTempC', String(inlet));
      }
      if (formData.pidErrorAttenC !== undefined) {
        const atten = Math.min(5, Math.max(0, Number(formData.pidErrorAttenC)));
        formDataToSubmit.set('pidErrorAttenC', String(atten));
      }
      if (formData.pidPdMuteEnabled) {
        formDataToSubmit.set('pidPdMuteEnabled', '1');
      }
      if (formData.pidPdMuteAboveC !== undefined) {
        const above = Math.min(10, Math.max(0, Number(formData.pidPdMuteAboveC)));
        formDataToSubmit.set('pidPdMuteAboveC', String(above));
      }
      if (formData.pidKiAbove !== undefined) {
        const kiAbove = Math.min(1000, Math.max(0, Number(formData.pidKiAbove)));
        formDataToSubmit.set('pidKiAbove', String(kiAbove));
      }
      const stableSec = Number(formData.stableDurationSec ?? 8);
      formDataToSubmit.set('stableDurationMs', String(Math.max(0, Math.round(stableSec * 1000))));

      if (formData.tempProbeFilterEnabled) {
        formDataToSubmit.set('tempProbeFilterEnabled', '1');
      }
      if (formData.tempProbeFilterAlpha !== undefined) {
        const alpha = Math.min(1, Math.max(0.01, Number(formData.tempProbeFilterAlpha)));
        formDataToSubmit.set('tempProbeFilterAlpha', String(alpha));
      }

      const flow1Bar = formDataToSubmit.get('pumpFlow1Bar');
      const flow9Bar = formDataToSubmit.get('pumpFlow9Bar');
      if (flow1Bar !== null && flow9Bar !== null) {
        formDataToSubmit.set('pumpModelCoeffs', `${flow1Bar},${flow9Bar}`);
      } else if (formData.pumpFlow1Bar !== undefined && formData.pumpFlow9Bar !== undefined) {
        // Fallback to React state if inputs aren't in the DOM (e.g. section hidden)
        formDataToSubmit.set('pumpModelCoeffs', `${formData.pumpFlow1Bar},${formData.pumpFlow9Bar}`);
      }
      formDataToSubmit.delete('pumpFlow1Bar');
      formDataToSubmit.delete('pumpFlow9Bar');

      // Ensure standbyBrightness is included even when the field is disabled
      if (!formData.standbyDisplayEnabled) {
        formDataToSubmit.set('standbyBrightness', '0');
      }

      if (restart) {
        formDataToSubmit.append('restart', '1');
      }
      const response = await fetch(form.action, {
        method: 'post',
        body: formDataToSubmit,
      });
      const data = await response.json();

      // Re-split `pid` the same way the initial load does. The server
      // returns the full `Kp,Ki,Kd,Kff` CSV; without splitting it here,
      // the next Save would combine `formData.pid` (already 4 fields)
      // with `formData.kf`, producing a 5-field CSV that grows on every
      // round-trip.
      const splitPid = data.pid ? splitPidString(data.pid) : null;

      // Only preserve standbyDisplayEnabled if brightness is greater than 0
      // If brightness is 0, let the useEffect recalculate it based on the saved value
      const updatedData = {
        ...data,
        ...(splitPid !== null ? { pid: splitPid.pid, kf: splitPid.kf } : {}),
        pidFreezeGraceSec: Math.round((data.pidFreezeGraceMs ?? 60000) / 1000),
        stableDurationSec: Math.round((data.stableDurationMs ?? 8000) / 1000),
        ...splitPumpModelCoeffs(data.pumpModelCoeffs),
        standbyDisplayEnabled: data.standbyBrightness > 0 ? formData.standbyDisplayEnabled : false,
      };

      setFormData(updatedData);
      setSubmitting(false);
    },
    [setFormData, formRef, formData, autowakeupSchedules],
  );

  const onExport = useCallback(() => {
    downloadJson(formData, 'settings.json');
  }, [formData]);

  const onUpload = function (evt) {
    if (evt.target.files.length) {
      const file = evt.target.files[0];
      const reader = new FileReader();
      reader.onload = async e => {
        const data = JSON.parse(e.target.result);
        setFormData(data);
      };
      reader.readAsText(file);
    }
  };

  if (isLoading) {
    return (
      <div className='flex w-full flex-row items-center justify-center py-16'>
        <Spinner size={8} />
      </div>
    );
  }

  return (
    <>
      <div className='mb-4 flex flex-row items-center gap-2'>
        <h2 className='flex-grow text-2xl font-bold sm:text-3xl'>Settings</h2>
        <button
          type='button'
          onClick={onExport}
          className='btn btn-ghost btn-sm'
          title='Export Settings'
        >
          <FontAwesomeIcon icon={faFileExport} />
        </button>
        <label
          htmlFor='settingsImport'
          className='btn btn-ghost btn-sm cursor-pointer'
          title='Import Settings'
        >
          <FontAwesomeIcon icon={faFileImport} />
        </label>
        <input
          onChange={onUpload}
          className='hidden'
          id='settingsImport'
          type='file'
          accept='.json,application/json'
        />
      </div>

      <form key='settings' ref={formRef} method='post' action='/api/settings' onSubmit={onSubmit}>
        <div className='grid grid-cols-1 gap-4 lg:grid-cols-10'>
          {/* Temperature Settings */}
          <Card sm={10} lg={5} title='Temperature Settings'>
            <div className='mb-4'>
              <label htmlFor='targetSteamTemp' className='mb-2 block text-sm font-medium'>
                Default Steam Temperature
              </label>
              <div className='input-group'>
                <label htmlFor='targetSteamTemp' className='input w-full'>
                  <input
                    id='targetSteamTemp'
                    name='targetSteamTemp'
                    type='number'
                    placeholder='135'
                    value={formData.targetSteamTemp}
                    onChange={onChange('targetSteamTemp')}
                  />
                  <span aria-label='celsius'>°C</span>
                </label>
              </div>
            </div>
            <div className='form-control'>
              <label htmlFor='targetWaterTemp' className='mb-2 block text-sm font-medium'>
                Default Water Temperature
              </label>
              <div className='input-group'>
                <label htmlFor='targetWaterTemp' className='input w-full'>
                  <input
                    id='targetWaterTemp'
                    name='targetWaterTemp'
                    type='number'
                    placeholder='80'
                    value={formData.targetWaterTemp}
                    onChange={onChange('targetWaterTemp')}
                  />
                  <span aria-label='celsius'>°C</span>
                </label>
              </div>
            </div>
          </Card>

          {/* User Preferences */}
          <Card sm={10} lg={5} title='User Preferences'>
            <div className='form-control mb-4'>
              <label htmlFor='startup-mode' className='mb-2 block text-sm font-medium'>
                Startup Mode
              </label>
              <select
                id='startup-mode'
                name='startupMode'
                className='select select-bordered w-full'
                onChange={onChange('startupMode')}
              >
                <option value='standby' selected={formData.startupMode === 'standby'}>
                  Standby
                </option>
                <option value='brew' selected={formData.startupMode === 'brew'}>
                  Brew
                </option>
              </select>
            </div>
            <div className='form-control mb-4'>
              <label htmlFor='standbyTimeout' className='mb-2 block text-sm font-medium'>
                Standby Timeout
              </label>
              <div className='input-group'>
                <label htmlFor='standbyTimeout' className='input w-full'>
                  <input
                    id='standbyTimeout'
                    name='standbyTimeout'
                    type='number'
                    placeholder='0'
                    value={formData.standbyTimeout}
                    onChange={onChange('standbyTimeout')}
                  />
                  <span aria-label='seconds'>s</span>
                </label>
              </div>
            </div>

            <div className='divider'>Predictive Scale Delay</div>
            <div className='mb-2 text-sm opacity-70'>
              Shuts off the process ahead of time based on the flow rate to account for any dripping
              or delays in the control.
            </div>
            <div className='form-control mb-4'>
              <label className='label cursor-pointer'>
                <span className='label-text'>Auto Adjust</span>
                <input
                  id='delayAdjust'
                  name='delayAdjust'
                  type='checkbox'
                  className='toggle toggle-primary'
                  checked={!!formData.delayAdjust}
                  onChange={onChange('delayAdjust')}
                />
              </label>
            </div>
            <div className='grid grid-cols-2 gap-4'>
              <div className='form-control'>
                <label htmlFor='brewDelay' className='mb-2 block text-sm font-medium'>
                  Brew
                </label>
                <div className='input-group'>
                  <label htmlFor='brewDelay' className='input w-full'>
                    <input
                      id='brewDelay'
                      name='brewDelay'
                      type='number'
                      step='any'
                      className='grow'
                      placeholder='0'
                      value={formData.brewDelay}
                      onChange={onChange('brewDelay')}
                    />
                    <span aria-label='milliseconds'>ms</span>
                  </label>
                </div>
              </div>
              <div className='form-control'>
                <label htmlFor='grindDelay' className='mb-2 block text-sm font-medium'>
                  Grind
                </label>
                <div className='input-group'>
                  <label htmlFor='grindDelay' className='input w-full'>
                    <input
                      id='grindDelay'
                      name='grindDelay'
                      type='number'
                      step='any'
                      className='grow'
                      placeholder='0'
                      value={formData.grindDelay}
                      onChange={onChange('grindDelay')}
                    />
                    <span aria-label='milliseconds'>ms</span>
                  </label>
                </div>
              </div>
            </div>

            <div className='divider'>Switch Control</div>
            <div className='form-control'>
              <label className='label cursor-pointer'>
                <span className='label-text'>Use momentary switches</span>
                <input
                  id='momentaryButtons'
                  name='momentaryButtons'
                  type='checkbox'
                  className='toggle toggle-primary'
                  checked={!!formData.momentaryButtons}
                  onChange={onChange('momentaryButtons')}
                />
              </label>
            </div>
          </Card>

          {/* Web Settings */}
          <Card sm={10} lg={5} title='Web Settings'>
            <div className='form-control mb-4'>
              <label htmlFor='webui-theme' className='label'>
                <span className='label-text font-medium'>Theme</span>
              </label>
              <select
                id='webui-theme'
                name='webui-theme'
                className='select select-bordered w-full'
                value={currentTheme}
                onChange={e => {
                  setCurrentTheme(e.target.value);
                  handleThemeChange(e);
                }}
              >
                <option value='light'>Light</option>
                <option value='dark'>Dark</option>
                <option value='coffee'>Coffee</option>
                <option value='nord'>Nord</option>
              </select>
            </div>
            <div className='form-control'>
              <label htmlFor='dashboardLayout' className='label'>
                <span className='label-text font-medium'>Dashboard Layout</span>
              </label>
              <select
                id='dashboardLayout'
                name='dashboardLayout'
                className='select select-bordered w-full'
                value={formData.dashboardLayout || DASHBOARD_LAYOUTS.ORDER_FIRST}
                onChange={e => {
                  setFormData({ ...formData, dashboardLayout: e.target.value });
                  setDashboardLayout(e.target.value);
                }}
              >
                <option value={DASHBOARD_LAYOUTS.ORDER_FIRST}>Process Controls First</option>
                <option value={DASHBOARD_LAYOUTS.ORDER_LAST}>Chart First</option>
              </select>
            </div>
          </Card>

          {/* System Preferences */}
          <Card sm={10} lg={5} title='System Preferences'>
            <div className='mb-4 rounded-lg border border-base-300 p-4'>
              <p className='mb-3 text-sm font-medium'>Wi-Fi status</p>
              <dl className='grid grid-cols-1 gap-2 text-sm sm:grid-cols-2'>
                <div>
                  <dt className='opacity-70'>State</dt>
                  <dd className='font-medium'>{getWifiStatusLabel(formData)}</dd>
                </div>
                <div>
                  <dt className='opacity-70'>Mode</dt>
                  <dd className='font-medium'>{formData.wifiMode || '—'}</dd>
                </div>
                <div>
                  <dt className='opacity-70'>IP address</dt>
                  <dd className='font-medium'>{formData.wifiIp || '—'}</dd>
                </div>
                <div>
                  <dt className='opacity-70'>Signal (RSSI)</dt>
                  <dd className='font-medium'>
                    {formData.wifiConnected && formData.wifiRssi ? `${formData.wifiRssi} dBm` : '—'}
                  </dd>
                </div>
                <div className='sm:col-span-2'>
                  <dt className='opacity-70'>Last disconnect reason</dt>
                  <dd className='font-medium'>{formData.wifiLastDisconnectReason || '—'}</dd>
                </div>
              </dl>
              <p className='mt-3 text-xs opacity-70'>
                If you see the <strong>GaggiMate</strong> Wi-Fi network, the machine is in AP fallback — connect to it
                and open http://4.4.4.1 to fix credentials.
              </p>
            </div>
            <div className='form-control mb-4'>
              <label htmlFor='wifiApTimeout' className='mb-2 block text-sm font-medium'>
                AP retry timeout (minutes)
              </label>
              <div className='input-group'>
                <label htmlFor='wifiApTimeout' className='input w-full'>
                  <input
                    id='wifiApTimeout'
                    name='wifiApTimeout'
                    type='number'
                    min='0'
                    className='grow'
                    placeholder='10'
                    value={formData.wifiApTimeout ?? 10}
                    onChange={onChange('wifiApTimeout')}
                  />
                  <span aria-label='minutes'>min</span>
                </label>
              </div>
              <p className='mt-1 text-xs opacity-70'>
                How long to keep retrying home Wi-Fi while in AP fallback. Set to 0 to retry forever.
              </p>
            </div>
            <div className='form-control mb-4'>
              <label htmlFor='wifiSsid' className='mb-2 block text-sm font-medium'>
                Wi-Fi SSID
              </label>
              <input
                id='wifiSsid'
                name='wifiSsid'
                type='text'
                className='input input-bordered w-full'
                placeholder='Wi-Fi SSID'
                value={formData.wifiSsid}
                onChange={onChange('wifiSsid')}
              />
            </div>
            <div className='form-control mb-4'>
              <label htmlFor='wifiPassword' className='mb-2 block text-sm font-medium'>
                Wi-Fi Password
              </label>
              <label className='input w-full'>
                <input
                  id='wifiPassword'
                  name='wifiPassword'
                  type={showWifiPassword ? 'text' : 'password'}
                  placeholder='Wi-Fi Password'
                  value={formData.wifiPassword}
                  onChange={onChange('wifiPassword')}
                />
                <span
                  className={`hover:text-primary cursor-pointer`}
                  aria-label='Show Password'
                  onClick={() => setShowWifiPassword(!showWifiPassword)}
                >
                  <FontAwesomeIcon icon={showWifiPassword ? faEyeSlash : faEye} />
                </span>
              </label>
            </div>
            <div className='form-control mb-4'>
              <label htmlFor='mdnsName' className='mb-2 block text-sm font-medium'>
                Hostname
              </label>
              <input
                id='mdnsName'
                name='mdnsName'
                type='text'
                className='input input-bordered w-full'
                placeholder='Hostname'
                value={formData.mdnsName}
                onChange={onChange('mdnsName')}
              />
            </div>
            <div className='form-control mb-4'>
              <label htmlFor='timezone' className='mb-2 block text-sm font-medium'>
                Time Zone
              </label>
              <select
                id='timezone'
                name='timezone'
                className='select select-bordered w-full'
                onChange={onChange('timezone')}
              >
                {timezones.map(tz => (
                  <option key={tz} value={tz} selected={formData.timezone === tz}>
                    {tz}
                  </option>
                ))}
              </select>
            </div>
            <div className='divider'>Clock</div>
            <div className='form-control'>
              <label className='label cursor-pointer'>
                <span className='label-text'>Use 24h Format</span>
                <input
                  id='clock24hFormat'
                  name='clock24hFormat'
                  type='checkbox'
                  className='toggle toggle-primary'
                  checked={!!formData.clock24hFormat}
                  onChange={onChange('clock24hFormat')}
                />
              </label>
            </div>
          </Card>

          {/* Machine Settings */}
          <Card sm={10} lg={5} title='Machine Settings'>
            <div className='form-control mb-4'>
              <label htmlFor='pid' className='mb-2 block text-sm font-medium'>
                PID Values
              </label>
              <div className='input-group'>
                <label htmlFor='pid' className='input w-full'>
                  <input
                    id='pid'
                    name='pid'
                    type='text'
                    className='grow'
                    placeholder='2.0, 0.1, 0.01'
                    value={formData.pid}
                    onChange={onChange('pid')}
                  />
                  <span>
                    K<sub>p</sub>, K<sub>i</sub>, K<sub>d</sub>
                  </span>
                </label>
              </div>
            </div>
            <div className='divider'>Idle PID (preheat)</div>
            <div className='form-control mb-4'>
              <label htmlFor='pidErrorAttenC' className='mb-2 block text-sm font-medium'>
                Near-target P/D softening (°C)
              </label>
              <input
                id='pidErrorAttenC'
                name='pidErrorAttenC'
                type='number'
                step='0.1'
                min='0'
                max='5'
                className='input input-bordered w-full'
                value={formData.pidErrorAttenC ?? 0}
                onChange={onChange('pidErrorAttenC')}
              />
              <p className='mt-2 text-xs opacity-70'>
                Scales down P and D when close to setpoint — less idle relay chatter; can soften the last part of a
                ramp. <strong>0 = off</strong>. Start <strong>1.0–1.5</strong>; try up to <strong>~2.5</strong> only if
                overshoot persists and ramps stay fast enough. Ki unchanged. Tune with PID Monitor.
              </p>
            </div>
            <div className='form-control mb-4'>
              <label className='label cursor-pointer'>
                <span className='label-text'>Mute P/D when above target</span>
                <input
                  id='pidPdMuteEnabled'
                  name='pidPdMuteEnabled'
                  type='checkbox'
                  className='toggle toggle-primary'
                  checked={formData.pidPdMuteEnabled === true}
                  onChange={onChange('pidPdMuteEnabled')}
                />
              </label>
              <p className='mt-1 text-xs opacity-70'>
                When current temp is more than the threshold below target, P and D go to zero and only I trims heat
                using <strong>Ki above</strong> — for passive cool-down after overshoot.
              </p>
            </div>
            <div className='form-control mb-4'>
              <label htmlFor='pidPdMuteAboveC' className='mb-2 block text-sm font-medium'>
                °C above target to mute P/D
              </label>
              <input
                id='pidPdMuteAboveC'
                name='pidPdMuteAboveC'
                type='number'
                step='0.1'
                min='0'
                max='10'
                className='input input-bordered w-full'
                disabled={formData.pidPdMuteEnabled !== true}
                value={formData.pidPdMuteAboveC ?? 0.5}
                onChange={onChange('pidPdMuteAboveC')}
              />
            </div>
            <div className='form-control mb-4'>
              <label htmlFor='pidKiAbove' className='mb-2 block text-sm font-medium'>
                Ki when above target
              </label>
              <input
                id='pidKiAbove'
                name='pidKiAbove'
                type='number'
                step='0.01'
                min='0'
                className='input input-bordered w-full'
                disabled={formData.pidPdMuteEnabled !== true}
                value={formData.pidKiAbove ?? formData.ki ?? 0.27}
                onChange={onChange('pidKiAbove')}
              />
              <p className='mt-2 text-xs opacity-70'>
                Separate from main Ki — used for I only while muted. Lower = slower bleed-down; higher = faster
                correction (may undershoot when normal PID resumes). Tune with PID Monitor (<code>pdMuted</code>,{' '}
                <code>kiActive</code>).
              </p>
            </div>
            <div className='form-control mb-4'>
              <label htmlFor='kf' className='mb-2 block text-sm font-medium'>
                Thermal Feedforward Gain
              </label>
              <div className='input-group'>
                <label htmlFor={'kf'} className={'input w-full'}>
                  <input
                    id='kf'
                    name='kf'
                    type='number'
                    step='0.001'
                    className='grow'
                    placeholder='0.600'
                    value={formData.kf}
                    onChange={onChange('kf')}
                  />
                  <span>
                    K<sub>ff</sub>
                  </span>
                </label>
              </div>
              <div className='mt-2 text-xs opacity-70'>
                Set to 0 to disable feedforward control.
              </div>
            </div>
            <div className='divider'>Thermal feedforward (Kff)</div>
            <div className='form-control mb-4'>
              <label className='label cursor-pointer'>
                <span className='label-text'>Enable disturbance feedforward (Kff)</span>
                <input
                  id='kffEnabled'
                  name='kffEnabled'
                  type='checkbox'
                  className='toggle toggle-primary'
                  checked={formData.kffEnabled !== false}
                  onChange={onChange('kffEnabled')}
                />
              </label>
              <p className='mt-1 text-xs opacity-70'>
                When on, K<sub>ff</sub> adds heat during flow on top of a <strong>latched I baseline</strong>.
                PID P/D updates are frozen then — PID is not off. Too much K<sub>ff</sub> shows up as a spike
                after the shot, not as PID overshoot during grace.
              </p>
            </div>
            <div className='form-control mb-4'>
              <label className='label cursor-pointer'>
                <span className='label-text'>Enable PID freeze during flow</span>
                <input
                  id='pidFreezeEnabled'
                  name='pidFreezeEnabled'
                  type='checkbox'
                  className='toggle toggle-primary'
                  checked={formData.pidFreezeEnabled !== false}
                  onChange={onChange('pidFreezeEnabled')}
                />
              </label>
              <p className='mt-1 text-xs opacity-70'>
                When on, P and D stop updating from the probe during flow; output uses a latched I baseline (plus K
                <sub>ff</sub> when enabled). When off, PID behaves like stock upstream during the shot.
              </p>
            </div>
            <div className='form-control mb-4'>
              <label className='label cursor-pointer'>
                <span className='label-text'>Enable post-shot PID freeze grace</span>
                <input
                  id='pidGraceEnabled'
                  name='pidGraceEnabled'
                  type='checkbox'
                  className='toggle toggle-primary'
                  checked={formData.pidGraceEnabled !== false}
                  onChange={onChange('pidGraceEnabled')}
                />
              </label>
              <p className='mt-1 text-xs opacity-70'>
                When on, the same latched I is held after flow stops for the grace duration below (no K
                <sub>ff</sub>). When off, freeze releases as soon as flow ends; the duration field is kept for when you
                turn grace back on.
              </p>
            </div>
            <div className='form-control mb-4'>
              <label htmlFor='incomingWaterTempC' className='mb-2 block text-sm font-medium'>
                Water inlet temperature (°C)
              </label>
              <input
                id='incomingWaterTempC'
                name='incomingWaterTempC'
                type='number'
                step='1'
                min='5'
                max='80'
                className='input input-bordered w-full'
                value={formData.incomingWaterTempC ?? 23}
                onChange={onChange('incomingWaterTempC')}
              />
              <p className='mt-1 text-xs opacity-70'>
                Estimated temperature at the boiler inlet for Kff (setpoint − inlet). On this
                machine, water pre-heats in the plumbing before the inlet — a fixed value is often
                wrong until an inlet probe is installed (see docs/thermal-kff-tuning.md in the
                repo).
              </p>
            </div>
            <div className='form-control mb-4'>
              <label htmlFor='pidFreezeGraceSec' className='mb-2 block text-sm font-medium'>
                Post-shot PID freeze grace (seconds)
              </label>
              <input
                id='pidFreezeGraceSec'
                name='pidFreezeGraceSec'
                type='number'
                step='1'
                min='0'
                className='input input-bordered w-full'
                disabled={formData.pidGraceEnabled === false}
                value={formData.pidFreezeGraceSec ?? 60}
                onChange={onChange('pidFreezeGraceSec')}
              />
              <p className='mt-1 text-xs opacity-70'>
                Used only when post-shot grace is enabled above. After flow stops: same latched I, no K
                <sub>ff</sub>, P/D still frozen — so PID cannot over-correct on a lagging probe. Set to 0 for an
                immediate release when grace is on.
              </p>
            </div>
            <div className='divider'>Idle vent &amp; boiler stability</div>
            <p className='mb-4 text-xs opacity-70'>
              Idle pressure vent runs only when the boiler is considered stable (settings below).
              While heating, the brew screen shows &quot;Stabilizing temperature&quot; and the valve
              stays closed.
            </p>

            <div className='mb-4 space-y-4 rounded-lg border border-base-300 p-4'>
              <p className='text-sm font-medium'>Boiler stability</p>
              <p className='text-xs opacity-70'>
                Temperature must stay within the band of the setpoint for the hold time before the
                machine is &quot;stable&quot; and idle vent is allowed.
              </p>
              <div className='form-control'>
                <label htmlFor='stableOffsetC' className='mb-2 block text-sm font-medium'>
                  Stable temperature band (°C)
                </label>
                <input
                  id='stableOffsetC'
                  name='stableOffsetC'
                  type='number'
                  step='0.1'
                  min='0'
                  className='input input-bordered w-full'
                  value={formData.stableOffsetC ?? 0.4}
                  onChange={onChange('stableOffsetC')}
                />
                <p className='mt-1 text-xs opacity-70'>
                  Max |temperature − setpoint| while counting toward stable.
                </p>
              </div>
              <div className='form-control'>
                <label htmlFor='stableDurationSec' className='mb-2 block text-sm font-medium'>
                  Stable hold time (seconds)
                </label>
                <input
                  id='stableDurationSec'
                  name='stableDurationSec'
                  type='number'
                  step='1'
                  min='0'
                  className='input input-bordered w-full'
                  value={formData.stableDurationSec ?? 8}
                  onChange={onChange('stableDurationSec')}
                />
                <p className='mt-1 text-xs opacity-70'>
                  How long temperature must stay inside the band before stable is declared.
                </p>
              </div>
            </div>

            <div className='mb-4 space-y-4 rounded-lg border border-base-300 p-4'>
              <p className='text-sm font-medium'>Idle pressure vent</p>
              <p className='text-xs opacity-70'>
                Between shots, opens the valve (pump off) to bleed trapped puck-line pressure when
                stable and pressure is high enough.
              </p>
              <div className='form-control'>
                <label className='label cursor-pointer px-0'>
                  <span className='label-text'>Enable idle pressure vent</span>
                  <input
                    id='ventEnabled'
                    name='ventEnabled'
                    type='checkbox'
                    className='toggle toggle-primary'
                    checked={formData.ventEnabled !== false}
                    onChange={onChange('ventEnabled')}
                  />
                </label>
              </div>
              <div className='form-control'>
                <label htmlFor='ventPressureBar' className='mb-2 block text-sm font-medium'>
                  Vent start pressure (bar)
                </label>
                <input
                  id='ventPressureBar'
                  name='ventPressureBar'
                  type='number'
                  step='0.01'
                  min='0'
                  className='input input-bordered w-full'
                  value={formData.ventPressureBar ?? 0.3}
                  onChange={onChange('ventPressureBar')}
                />
                <p className='mt-1 text-xs opacity-70'>
                  Latch vent on when idle pressure rises above this (pump stays off).
                </p>
              </div>
              <div className='form-control'>
                <label htmlFor='ventPressureLowBar' className='mb-2 block text-sm font-medium'>
                  Vent clear pressure (bar)
                </label>
                <input
                  id='ventPressureLowBar'
                  name='ventPressureLowBar'
                  type='number'
                  step='0.01'
                  min='0'
                  className='input input-bordered w-full'
                  value={formData.ventPressureLowBar ?? 0.01}
                  onChange={onChange('ventPressureLowBar')}
                />
                <p className='mt-1 text-xs opacity-70'>
                  Close the valve when pressure drops below this. Default 0.01 bleeds almost to
                  zero; try 0.10–0.20 bar for a shorter hiss if venting feels too long. Must be
                  lower than vent start pressure.
                </p>
              </div>
            </div>
            {showPumpConfig.value && (
              <>
                <div className='divider'>Pump configuration</div>
                <label htmlFor='pumpFlow1Bar' className='mb-2 block text-sm font-medium'>
                Pump Flow Coefficients
              </label>
              <div className='mb-2 text-xs opacity-70'>
                Enter 2 values (flow at 1 bar (10.205 default), flow at 9 bar (5.521 default))
              </div>
              <div className='grid grid-cols-1 gap-4 sm:grid-cols-2'>
                <div className='form-control'>
                  <label htmlFor='pumpFlow1Bar' className='mb-2 block text-sm font-medium'>
                    Flow at 1 bar
                  </label>
                  <div className='input-group'>
                    <label htmlFor='pumpFlow1Bar' className='input w-full'>
                      <input
                        id='pumpFlow1Bar'
                        name='pumpFlow1Bar'
                        type='number'
                        step='0.001'
                        className='grow'
                        placeholder='10.205'
                        value={formData.pumpFlow1Bar}
                        onChange={onChange('pumpFlow1Bar')}
                      />
                      <span>ml/s</span>
                    </label>
                  </div>
                </div>
                <div className='form-control'>
                  <label htmlFor='pumpFlow9Bar' className='mb-2 block text-sm font-medium'>
                    Flow at 9 bar
                  </label>
                  <div className='input-group'>
                    <label htmlFor='pumpFlow9Bar' className='input w-full'>
                      <input
                        id='pumpFlow9Bar'
                        name='pumpFlow9Bar'
                        type='number'
                        step='0.001'
                        className='grow'
                        placeholder='5.521'
                        value={formData.pumpFlow9Bar}
                        onChange={onChange('pumpFlow9Bar')}
                      />
                      <span>ml/s</span>
                    </label>
                  </div>
                </div>
              </div>
              </>
            )}
            <div className='form-control mb-4'>
              <label className='label cursor-pointer'>
                <span className='label-text'>Enable probe smoothing</span>
                <input
                  id='tempProbeFilterEnabled'
                  name='tempProbeFilterEnabled'
                  type='checkbox'
                  className='toggle toggle-primary'
                  checked={formData.tempProbeFilterEnabled !== false}
                  onChange={onChange('tempProbeFilterEnabled')}
                />
              </label>
              <p className='mt-2 text-xs opacity-70'>
                Smooths boiler probe readings before PID and graphs. <strong>Alpha 0.01–1.0:</strong> lower values react
                more slowly (less noise); higher values track faster. Default <strong>0.05</strong>. Turn off only for
                diagnosis — PID may chatter. Separate from idle P/D softening and the controller&apos;s derivative
                filter.
              </p>
            </div>
            {formData.tempProbeFilterEnabled !== false && (
              <div className='form-control mb-4'>
                <label htmlFor='tempProbeFilterAlpha' className='mb-2 block text-sm font-medium'>
                  Smoothing alpha
                </label>
                <input
                  id='tempProbeFilterAlpha'
                  name='tempProbeFilterAlpha'
                  type='number'
                  step='0.01'
                  min='0.01'
                  max='1'
                  className='input input-bordered w-full'
                  value={formData.tempProbeFilterAlpha ?? 0.05}
                  onChange={onChange('tempProbeFilterAlpha')}
                />
              </div>
            )}
            <div className='form-control mb-4'>
              <label htmlFor='temperatureOffset' className='mb-2 block text-sm font-medium'>
                Temperature Offset (°C)
              </label>
              <div className='input-group'>
                <label htmlFor='temperatureOffset' className='input w-full'>
                  <input
                    id='temperatureOffset'
                    name='temperatureOffset'
                    type='number'
                    step='any'
                    className='grow'
                    placeholder='0'
                    value={formData.temperatureOffset}
                    onChange={onChange('temperatureOffset')}
                  />
                  <span aria-label='celsius'>°C</span>
                </label>
              </div>
            </div>
            {pressureAvailable.value && (
              <div className='form-control mb-4'>
                <label htmlFor='pressureScaling' className='mb-2 block text-sm font-medium'>
                  Pressure Sensor Rating
                </label>
                <div className='mb-2 text-xs opacity-70'>
                  Enter the bar rating of the pressure sensor being used
                </div>
                <div className='input-group'>
                  <label htmlFor='pressureScaling' className='input w-full'>
                    <input
                      id='pressureScaling'
                      name='pressureScaling'
                      type='number'
                      step='any'
                      className='grow'
                      placeholder='0.0'
                      value={formData.pressureScaling}
                      onChange={onChange('pressureScaling')}
                    />
                    <span>bar</span>
                  </label>
                </div>
              </div>
            )}
            <div className='form-control mb-4'>
              <label htmlFor='steamPumpPercentage' className='mb-2 block text-sm font-medium'>
                Steam Pump Assist
              </label>
              <div className='mb-2 text-xs opacity-70'>
                {pressureAvailable.value
                  ? 'How many ml/s to pump into the boiler during steaming'
                  : 'What percentage to run the pump at during steaming'}
              </div>
              <div className='input-group'>
                <label htmlFor='steamPumpPercentage' className='input w-full'>
                  <input
                    id='steamPumpPercentage'
                    name='steamPumpPercentage'
                    type='number'
                    step='0.1'
                    className='grow'
                    placeholder={pressureAvailable.value ? '0.0' : '0.0 %'}
                    value={String(
                      formData.steamPumpPercentage * (pressureAvailable.value ? 0.1 : 1),
                    )}
                    onBlur={e =>
                      setFormData({
                        ...formData,
                        steamPumpPercentage: (
                          parseFloat(e.target.value) * (pressureAvailable.value ? 10 : 1)
                        ).toFixed(0),
                      })
                    }
                  />
                  <span aria-label={pressureAvailable.value ? 'milliliter per second' : 'percent'}>
                    {pressureAvailable.value ? 'ml/s' : '%'}
                  </span>
                </label>
              </div>
            </div>
            {pressureAvailable.value && (
              <div className='form-control mb-4'>
                <label htmlFor='steamPumpCutoff' className='mb-2 block text-sm font-medium'>
                  Pump Assist Cutoff
                </label>
                <div className='mb-2 text-xs opacity-70'>
                  At how many bars should the pump assist stop. This makes it so the pump will only
                  run when steam is flowing.
                </div>
                <div className='input-group'>
                  <label htmlFor='steamPumpCutoff' className='input w-full'>
                    <input
                      id='steamPumpCutoff'
                      name='steamPumpCutoff'
                      type='number'
                      step='any'
                      className='grow'
                      placeholder='0.0'
                      value={formData.steamPumpCutoff}
                      onChange={onChange('steamPumpCutoff')}
                    />
                    <span>bar</span>
                  </label>
                </div>
              </div>
            )}
            <div className='form-control'>
              <label htmlFor='altRelayFunction' className='mb-2 block text-sm font-medium'>
                Alt Relay / SSR2 Function
              </label>
              <select
                id='altRelayFunction'
                name='altRelayFunction'
                className='select select-bordered w-full'
                value={formData.altRelayFunction ?? 1}
                onChange={onChange('altRelayFunction')}
              >
                <option value={0}>None</option>
                <option value={1}>Grind</option>
                <option value={2} disabled className='text-gray-400'>
                  Steam Boiler (Coming Soon)
                </option>
              </select>
            </div>
          </Card>

          {/* Display Settings */}
          <Card sm={10} lg={5} title='Display Settings'>
            <div className='form-control mb-4'>
              <label htmlFor='mainBrightness' className='mb-2 block text-sm font-medium'>
                Main Brightness (1-16)
              </label>
              <input
                id='mainBrightness'
                name='mainBrightness'
                type='number'
                className='input input-bordered w-full'
                placeholder='16'
                min='1'
                max='16'
                value={formData.mainBrightness}
                onChange={onChange('mainBrightness')}
              />
            </div>
            <div className='divider'>Standby Display</div>
            <div className='form-control mb-4'>
              <label className='label cursor-pointer'>
                <span className='label-text'>Enable standby display</span>
                <input
                  id='standbyDisplayEnabled'
                  name='standbyDisplayEnabled'
                  type='checkbox'
                  className='toggle toggle-primary'
                  checked={formData.standbyDisplayEnabled}
                  onChange={onChange('standbyDisplayEnabled')}
                />
              </label>
            </div>
            <div className='form-control mb-4'>
              <label htmlFor='standbyBrightness' className='mb-2 block text-sm font-medium'>
                Standby Brightness (0-16)
              </label>
              <input
                id='standbyBrightness'
                name='standbyBrightness'
                type='number'
                className='input input-bordered w-full'
                placeholder='8'
                min='0'
                max='16'
                value={formData.standbyBrightness}
                onChange={onChange('standbyBrightness')}
                disabled={!formData.standbyDisplayEnabled}
              />
            </div>
            <div className='form-control mb-4'>
              <label htmlFor='standbyBrightnessTimeout' className='mb-2 block text-sm font-medium'>
                Standby Brightness Timeout (s)
              </label>
              <div className='input-group'>
                <label htmlFor='standbyBrightnessTimeout' className='input w-full'>
                  <input
                    id='standbyBrightnessTimeout'
                    name='standbyBrightnessTimeout'
                    type='number'
                    className='grow'
                    placeholder='60'
                    min='1'
                    value={formData.standbyBrightnessTimeout}
                    onChange={onChange('standbyBrightnessTimeout')}
                  />
                  <span aria-label='seconds'>s</span>
                </label>
              </div>
            </div>
            <div className='form-control'>
              <label htmlFor='themeMode' className='mb-2 block text-sm font-medium'>
                Theme
              </label>
              <select
                id='themeMode'
                name='themeMode'
                className='select select-bordered w-full'
                value={formData.themeMode}
                onChange={onChange('themeMode')}
              >
                <option value={0}>Dark Theme</option>
                <option value={1}>Light Theme</option>
              </select>
            </div>
          </Card>

          {/* Sunrise Settings */}
          {ledControl.value && (
            <Card sm={10} lg={5} title='Sunrise Settings'>
              <div className='mb-2 text-sm opacity-70'>
                Set the colors for the LEDs when in idle mode with no warnings.
              </div>
              <div className='mb-4 grid grid-cols-2 gap-4'>
                <div className='form-control'>
                  <label htmlFor='sunriseR' className='mb-2 block text-sm font-medium'>
                    Red (0 - 255)
                  </label>
                  <input
                    id='sunriseR'
                    name='sunriseR'
                    type='number'
                    className='input input-bordered w-full'
                    placeholder='16'
                    value={formData.sunriseR}
                    onChange={onChange('sunriseR')}
                  />
                </div>
                <div className='form-control'>
                  <label htmlFor='sunriseG' className='mb-2 block text-sm font-medium'>
                    Green (0 - 255)
                  </label>
                  <input
                    id='sunriseG'
                    name='sunriseG'
                    type='number'
                    className='input input-bordered w-full'
                    placeholder='16'
                    value={formData.sunriseG}
                    onChange={onChange('sunriseG')}
                  />
                </div>
                <div className='form-control'>
                  <label htmlFor='sunriseB' className='mb-2 block text-sm font-medium'>
                    Blue (0 - 255)
                  </label>
                  <input
                    id='sunriseB'
                    name='sunriseB'
                    type='number'
                    className='input input-bordered w-full'
                    placeholder='16'
                    value={formData.sunriseB}
                    onChange={onChange('sunriseB')}
                  />
                </div>
                <div className='form-control'>
                  <label htmlFor='sunriseW' className='mb-2 block text-sm font-medium'>
                    White (0 - 255)
                  </label>
                  <input
                    id='sunriseW'
                    name='sunriseW'
                    type='number'
                    className='input input-bordered w-full'
                    placeholder='16'
                    value={formData.sunriseW}
                    onChange={onChange('sunriseW')}
                  />
                </div>
              </div>
              <div className='form-control mb-4'>
                <label htmlFor='sunriseExtBrightness' className='mb-2 block text-sm font-medium'>
                  External LED (0 - 255)
                </label>
                <input
                  id='sunriseExtBrightness'
                  name='sunriseExtBrightness'
                  type='number'
                  className='input input-bordered w-full'
                  placeholder='16'
                  value={formData.sunriseExtBrightness}
                  onChange={onChange('sunriseExtBrightness')}
                />
              </div>
              <div className='form-control'>
                <label htmlFor='emptyTankDistance' className='mb-2 block text-sm font-medium'>
                  Distance between ToF sensor and bottom of the tank
                </label>
                <div className='flex flex-row gap-2'>
                  <div className='input-group flex-grow'>
                    <label htmlFor='emptyTankDistance' className='input w-full'>
                      <input
                        id='emptyTankDistance'
                        name='emptyTankDistance'
                        type='number'
                        className='grow'
                        placeholder='16'
                        value={formData.emptyTankDistance}
                        onChange={onChange('emptyTankDistance')}
                      />
                      <span aria-label='millimeter'>mm</span>
                    </label>
                  </div>
                  <div>
                    <Tooltip content={`Set to current measurement: ${tofDistance}mm`}>
                      <button
                        className='btn btn-ghost'
                        onClick={() =>
                          setFormData({
                            ...formData,
                            emptyTankDistance: tofDistance,
                          })
                        }
                      >
                        <FontAwesomeIcon icon={faCrosshairs} />
                      </button>
                    </Tooltip>
                  </div>
                </div>
              </div>
              <div className='form-control'>
                <label htmlFor='fullTankDistance' className='mb-2 block text-sm font-medium'>
                  Distance between ToF sensor and the max line of the tank
                </label>
                <div className='flex flex-row gap-2'>
                  <div className='input-group flex-grow'>
                    <label htmlFor='fullTankDistance' className='input w-full'>
                      <input
                        id='fullTankDistance'
                        name='fullTankDistance'
                        type='number'
                        className='grow'
                        placeholder='16'
                        value={formData.fullTankDistance}
                        onChange={onChange('fullTankDistance')}
                      />
                      <span aria-label='millimeter'>mm</span>
                    </label>
                  </div>
                  <div>
                    <Tooltip content={`Set to current measurement: ${tofDistance}mm`}>
                      <button
                        className='btn btn-ghost'
                        onClick={() =>
                          setFormData({
                            ...formData,
                            fullTankDistance: tofDistance,
                          })
                        }
                      >
                        <FontAwesomeIcon icon={faCrosshairs} />
                      </button>
                    </Tooltip>
                  </div>
                </div>
              </div>
            </Card>
          )}

          <Card sm={10} title='Plugins'>
            <PluginCard
              formData={formData}
              onChange={onChange}
              autowakeupSchedules={autowakeupSchedules}
              addAutoWakeupSchedule={addAutoWakeupSchedule}
              removeAutoWakeupSchedule={removeAutoWakeupSchedule}
              updateAutoWakeupTime={updateAutoWakeupTime}
              updateAutoWakeupEndTime={updateAutoWakeupEndTime}
              updateAutoWakeupDay={updateAutoWakeupDay}
            />
          </Card>
        </div>

        <div className='pt-4 lg:col-span-10'>
          <div className='alert alert-warning shadow-sm'>
            <span>Some options like Wi-Fi, NTP, and managing plugins require a restart.</span>
          </div>
          <div className='flex flex-col gap-2 pt-4 sm:flex-row'>
            <a href='/' className='btn btn-outline'>
              Back
            </a>
            <button type='submit' className='btn btn-primary' disabled={submitting}>
              {submitting && <Spinner size={4} />} Save
            </button>
            <button
              type='submit'
              name='restart'
              className='btn btn-secondary'
              disabled={submitting}
              onClick={e => onSubmit(e, true)}
            >
              Save and Restart
            </button>
          </div>
        </div>
      </form>
    </>
  );
}
