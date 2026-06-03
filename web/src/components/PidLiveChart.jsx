import { machine } from '../services/ApiService.js';
import { Chart } from 'chart.js';
import { ChartComponent } from './Chart.jsx';

const TIME_WINDOW_MS = 180000;

export function getPidChartData(data) {
  const end = new Date();
  end.setMilliseconds(0);
  const start = new Date(end.getTime() - TIME_WINDOW_MS);
  start.setMilliseconds(0);

  const filteredData = data.filter(item => item.timestamp >= start && item.timestamp <= end);

  const tempValues = filteredData.flatMap(i => [i.currentTemperature, i.targetTemperature]);
  const tempMin = tempValues.length > 0 ? Math.max(0, Math.floor(Math.min(...tempValues) - 5)) : 0;
  const tempMax = tempValues.length > 0 ? Math.ceil(Math.max(...tempValues) + 10) : 160;

  const pidValues = filteredData.flatMap(i => [i.pidP, i.pidI, i.pidD, i.pidKff, i.pidOut]);
  const pidMin =
    pidValues.length > 0 ? Math.floor(Math.min(...pidValues, 0) - 10) : 0;
  const pidMax =
    pidValues.length > 0 ? Math.ceil(Math.max(...pidValues, 1000) + 20) : 1000;

  const point = (getter) =>
    filteredData.map(i => ({ x: i.timestamp.toISOString(), y: getter(i) }));

  return {
    type: 'line',
    data: {
      datasets: [
        {
          label: 'Current Temperature',
          borderColor: '#F0561D',
          pointStyle: false,
          data: point(i => i.currentTemperature),
        },
        {
          label: 'Target Temperature',
          borderColor: '#731F00',
          borderDash: [6, 6],
          pointStyle: false,
          data: point(i => i.targetTemperature),
        },
        {
          label: 'PID P',
          borderColor: '#0066CC',
          pointStyle: false,
          yAxisID: 'y1',
          data: point(i => i.pidP),
        },
        {
          label: 'PID I',
          borderColor: '#63993D',
          pointStyle: false,
          yAxisID: 'y1',
          data: point(i => i.pidI),
        },
        {
          label: 'PID D',
          borderColor: '#8B5CF6',
          pointStyle: false,
          yAxisID: 'y1',
          data: point(i => i.pidD),
        },
        {
          label: 'PID Kff',
          borderColor: '#F59E0B',
          pointStyle: false,
          yAxisID: 'y1',
          data: point(i => i.pidKff),
        },
        {
          label: 'Heater out',
          borderColor: '#DC2626',
          pointStyle: false,
          yAxisID: 'y1',
          data: point(i => i.pidOut),
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          position: 'top',
          display: true,
          labels: {
            usePointStyle: true,
            pointStyle: 'line',
            pointStyleWidth: 20,
            padding: 8,
            font: {
              size: window.innerWidth < 640 ? 10 : 12,
            },
            generateLabels(chart) {
              const original = Chart.defaults.plugins.legend.labels.generateLabels;
              const labels = original.call(this, chart);
              labels.forEach((label, index) => {
                const dataset = chart.data.datasets[index];
                label.lineWidth = 3;
                if (dataset.borderDash?.length) {
                  label.lineDash = dataset.borderDash;
                }
              });
              return labels;
            },
          },
        },
        title: {
          display: true,
          text: 'PID Live — 3 min window',
          font: {
            size: window.innerWidth < 640 ? 14 : 16,
          },
        },
      },
      animation: false,
      scales: {
        y: {
          type: 'linear',
          min: tempMin,
          max: tempMax,
          ticks: {
            stepSize: 5,
            font: {
              size: window.innerWidth < 640 ? 10 : 12,
            },
            callback: value => `${Math.round(value)} °C`,
          },
        },
        y1: {
          type: 'linear',
          min: pidMin,
          max: pidMax,
          position: 'right',
          ticks: {
            font: {
              size: window.innerWidth < 640 ? 10 : 12,
            },
            callback: value => Math.round(value * 10) / 10,
          },
        },
        x: {
          type: 'time',
          min: start,
          max: end,
          time: {
            unit: 'second',
            stepSize: 1,
            displayFormats: {
              second: 'HH:mm:ss',
            },
          },
          ticks: {
            source: 'auto',
            autoSkip: true,
            callback: value => {
              const diff = Math.ceil((end.getTime() - value) / 1000);
              return `-${diff}s`;
            },
            font: {
              size: window.innerWidth < 640 ? 10 : 12,
            },
            maxTicksLimit: 8,
          },
        },
      },
    },
  };
}

export function PidLiveChart() {
  const chartData = getPidChartData(machine.value.history);

  return (
    <ChartComponent
      className='h-full min-h-[200px] w-full flex-1 lg:min-h-[350px]'
      chartClassName='h-full w-full'
      data={chartData}
    />
  );
}
