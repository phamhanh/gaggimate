import {
  Chart,
  LineController,
  TimeScale,
  LinearScale,
  PointElement,
  LineElement,
  Legend,
  Filler,
} from 'chart.js';
import 'chartjs-adapter-dayjs-4/dist/chartjs-adapter-dayjs-4.esm';
import { useQuery } from 'preact-fetching';
import { machine } from '../../services/ApiService.js';
import Card from '../../components/Card.jsx';
import { PidLiveChart } from '../../components/PidLiveChart.jsx';
import { PidStatusPanel } from '../../components/PidStatusPanel.jsx';
import {
  MAX_PID_MONITOR_WINDOW_MINUTES,
  MIN_PID_MONITOR_WINDOW_MINUTES,
  pidMonitorWindowMinutes,
  setPidMonitorWindowMinutes,
} from '../../utils/pidMonitorManager.js';

Chart.register(LineController, TimeScale, LinearScale, PointElement, LineElement, Filler, Legend);

export function PidMonitor() {
  const connected = machine.value.connected;
  const { data: pidSettings } = useQuery('pid-monitor-settings', async () => {
    const response = await fetch('/api/settings');
    return response.json();
  }, {
    staleTime: 30000,
    refetchOnWindowFocus: false,
  });

  const onWindowChange = event => {
    setPidMonitorWindowMinutes(event.currentTarget.value);
  };

  return (
    <>
      <div className='mb-4 flex flex-row items-center gap-2'>
        <h1 className='flex-grow text-2xl font-bold sm:text-3xl'>PID Live Monitor</h1>
        <label className='flex items-center gap-2 text-sm'>
          <span className='text-base-content/70 whitespace-nowrap'>Window</span>
          <input
            type='number'
            className='input input-bordered input-sm w-20'
            min={MIN_PID_MONITOR_WINDOW_MINUTES}
            max={MAX_PID_MONITOR_WINDOW_MINUTES}
            step='1'
            value={pidMonitorWindowMinutes.value}
            onInput={onWindowChange}
            aria-label='Chart window in minutes'
          />
          <span className='text-base-content/70'>min</span>
        </label>
      </div>

      {!connected && (
        <div role='alert' className='alert alert-warning mb-4'>
          <span>Disconnected — waiting for WebSocket status updates.</span>
        </div>
      )}

      <div className='grid grid-cols-1 gap-4 lg:grid-cols-10 lg:items-stretch'>
        <Card sm={10} lg={7} title='Temperature & PID' fullHeight={true}>
          <PidLiveChart />
        </Card>

        <Card sm={10} lg={3} title='Live values'>
          <PidStatusPanel pidSettings={pidSettings} />
        </Card>
      </div>
    </>
  );
}
