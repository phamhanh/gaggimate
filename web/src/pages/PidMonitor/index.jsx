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
import { machine } from '../../services/ApiService.js';
import Card from '../../components/Card.jsx';
import { PidLiveChart } from '../../components/PidLiveChart.jsx';
import { PidStatusPanel } from '../../components/PidStatusPanel.jsx';

Chart.register(LineController, TimeScale, LinearScale, PointElement, LineElement, Filler, Legend);

export function PidMonitor() {
  const connected = machine.value.connected;

  return (
    <>
      <div className='mb-4 flex flex-row items-center gap-2'>
        <h1 className='flex-grow text-2xl font-bold sm:text-3xl'>PID Live Monitor</h1>
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
          <PidStatusPanel />
        </Card>
      </div>
    </>
  );
}
