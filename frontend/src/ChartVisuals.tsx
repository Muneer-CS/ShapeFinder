import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { TimeSeriesResponse } from './api/marketData'

type OverlayPoint = { index: number; reference: number; match: number }

type Props =
  | {
      kind: 'price'
      series: TimeSeriesResponse
      label: string
      compact?: boolean
    }
  | {
      kind: 'overlay'
      overlay: OverlayPoint[]
      referenceSymbol: string
      matchSymbol: string
      label: string
    }

const intraday = (interval: TimeSeriesResponse['interval']) =>
  interval !== '1day'
const displayDate = (value: string, interval: TimeSeriesResponse['interval']) =>
  new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    ...(intraday(interval) ? { timeStyle: 'short' as const } : {}),
  }).format(
    new Date(intraday(interval) ? value : `${value.slice(0, 10)}T12:00:00`),
  )

export default function ChartVisuals(props: Props) {
  if (props.kind === 'overlay') {
    return (
      <div className="overlay-chart" role="img" aria-label={props.label}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart
            data={props.overlay}
            margin={{ top: 10, right: 18, bottom: 4, left: 0 }}
          >
            <CartesianGrid stroke="#203a33" vertical={false} />
            <XAxis dataKey="index" hide />
            <YAxis
              tick={{ fill: '#82968f', fontSize: 12 }}
              axisLine={false}
              tickLine={false}
              width={52}
            />
            <Tooltip contentStyle={tooltipStyle} />
            <Legend />
            <Line
              type="monotone"
              dataKey="reference"
              name={props.referenceSymbol}
              stroke="#6ee7b7"
              strokeWidth={2.5}
              dot={false}
              isAnimationActive={false}
            />
            <Line
              type="monotone"
              dataKey="match"
              name={props.matchSymbol}
              stroke="#f4c95d"
              strokeWidth={2.5}
              dot={false}
              isAnimationActive={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    )
  }

  const data = props.series.bars.map((bar) => ({
    timestamp: bar.timestamp,
    close: Number(bar.close),
  }))
  return (
    <div
      className={`chart${props.compact ? ' chart-compact' : ''}`}
      role="img"
      aria-label={props.label}
    >
      <ResponsiveContainer width="100%" height="100%">
        <LineChart
          data={data}
          margin={{ top: 12, right: 18, bottom: 8, left: 2 }}
        >
          <CartesianGrid stroke="#203a33" vertical={false} />
          <XAxis
            dataKey="timestamp"
            tickFormatter={(value: string) =>
              displayDate(value, props.series.interval)
            }
            minTickGap={48}
            tick={{ fill: '#82968f', fontSize: 12 }}
            axisLine={false}
            tickLine={false}
          />
          <YAxis
            domain={['auto', 'auto']}
            tick={{ fill: '#82968f', fontSize: 12 }}
            axisLine={false}
            tickLine={false}
            width={58}
            tickFormatter={(value: number) =>
              value.toLocaleString(undefined, { maximumFractionDigits: 2 })
            }
          />
          <Tooltip
            labelFormatter={(value) =>
              displayDate(String(value), props.series.interval)
            }
            formatter={(value) => [
              Number(value).toLocaleString(undefined, {
                maximumFractionDigits: 4,
              }),
              'Close',
            ]}
            contentStyle={tooltipStyle}
          />
          <Line
            type="monotone"
            dataKey="close"
            stroke="#6ee7b7"
            strokeWidth={2.5}
            dot={data.length < 3}
            activeDot={{ r: 5 }}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

const tooltipStyle = {
  background: '#10231e',
  border: '1px solid #35564d',
  borderRadius: 8,
}
