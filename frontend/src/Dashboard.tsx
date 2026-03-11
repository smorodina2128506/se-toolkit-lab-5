import { useState, useEffect } from 'react'
import {
  Bar,
  Line,
} from 'react-chartjs-2'
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  BarElement,
  LineElement,
  PointElement,
  Title,
  Tooltip,
  Legend,
} from 'chart.js'

ChartJS.register(
  CategoryScale,
  LinearScale,
  BarElement,
  LineElement,
  PointElement,
  Title,
  Tooltip,
  Legend,
)

const STORAGE_KEY = 'api_key'

interface ScoreBucket {
  bucket: string
  count: number
}

interface TimelineEntry {
  date: string
  submissions: number
}

interface PassRateEntry {
  task: string
  avg_score: number
  attempts: number
}

const LAB_OPTIONS = ['lab-01', 'lab-02', 'lab-03', 'lab-04', 'lab-05']

function getAuthHeader(): string {
  const token = localStorage.getItem(STORAGE_KEY)
  return token ? `Bearer ${token}` : ''
}

async function fetchJson<T>(url: string): Promise<T> {
  const headers: Record<string, string> = {}
  const auth = getAuthHeader()
  if (auth) headers['Authorization'] = auth

  const res = await fetch(url, { headers })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json() as Promise<T>
}

export function Dashboard() {
  const [lab, setLab] = useState('lab-04')
  const [scores, setScores] = useState<ScoreBucket[] | null>(null)
  const [timeline, setTimeline] = useState<TimelineEntry[] | null>(null)
  const [passRates, setPassRates] = useState<PassRateEntry[] | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!getAuthHeader()) return

    setLoading(true)
    setError(null)

    const scoresUrl = `/analytics/scores?lab=${encodeURIComponent(lab)}`
    const timelineUrl = `/analytics/timeline?lab=${encodeURIComponent(lab)}`
    const passRatesUrl = `/analytics/pass-rates?lab=${encodeURIComponent(lab)}`

    Promise.all([
      fetchJson<ScoreBucket[]>(scoresUrl),
      fetchJson<TimelineEntry[]>(timelineUrl),
      fetchJson<PassRateEntry[]>(passRatesUrl),
    ])
      .then(([s, t, p]) => {
        setScores(s)
        setTimeline(t)
        setPassRates(p)
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false))
  }, [lab])

  if (loading) return <p>Loading...</p>
  if (error) return <p>Error: {error}</p>

  const barData = scores
    ? {
        labels: scores.map((s) => s.bucket),
        datasets: [
          {
            label: 'Count',
            data: scores.map((s) => s.count),
            backgroundColor: 'rgba(54, 162, 235, 0.5)',
          },
        ],
      }
    : { labels: [], datasets: [] }

  const lineData = timeline
    ? {
        labels: timeline.map((t) => t.date),
        datasets: [
          {
            label: 'Submissions',
            data: timeline.map((t) => t.submissions),
            borderColor: 'rgb(75, 192, 192)',
            tension: 0.1,
          },
        ],
      }
    : { labels: [], datasets: [] }

  return (
    <div className="dashboard">
      <div className="dashboard-controls">
        <label htmlFor="lab-select">Lab:</label>
        <select
          id="lab-select"
          value={lab}
          onChange={(e) => setLab(e.target.value)}
        >
          {LAB_OPTIONS.map((opt) => (
            <option key={opt} value={opt}>
              {opt}
            </option>
          ))}
        </select>
      </div>

      <section className="dashboard-section">
        <h2>Score Distribution</h2>
        <div className="chart-container">
          <Bar data={barData} options={{ responsive: true }} />
        </div>
      </section>

      <section className="dashboard-section">
        <h2>Submissions Over Time</h2>
        <div className="chart-container">
          <Line data={lineData} options={{ responsive: true }} />
        </div>
      </section>

      <section className="dashboard-section">
        <h2>Pass Rates by Task</h2>
        {passRates && passRates.length > 0 ? (
          <table>
            <thead>
              <tr>
                <th>Task</th>
                <th>Avg Score</th>
                <th>Attempts</th>
              </tr>
            </thead>
            <tbody>
              {passRates.map((row) => (
                <tr key={row.task}>
                  <td>{row.task}</td>
                  <td>{row.avg_score.toFixed(1)}</td>
                  <td>{row.attempts}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p>No pass rate data</p>
        )}
      </section>
    </div>
  )
}
