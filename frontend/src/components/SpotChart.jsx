import { useEffect, useRef, useState } from "react";
import { createChart, CandlestickSeries } from "lightweight-charts";

const IST_OFFSET_SEC = 5.5 * 3600;
const CANDLE_INTERVAL_SEC = 15 * 60;
const DAY_LABELS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

const DISPLAY_NAMES = {
  NIFTY: "NIFTY 50",
  SENSEX: "SENSEX",
};

function buildDayBreakMarkers(candles) {
  const markers = [];
  let prevDay = null;
  for (const c of candles) {
    const d = new Date((c.time + IST_OFFSET_SEC) * 1000);
    const dayKey = d.getUTCDate();
    if (prevDay !== null && dayKey !== prevDay) {
      const dd = String(d.getUTCDate()).padStart(2, "0");
      const mm = String(d.getUTCMonth() + 1).padStart(2, "0");
      const dayName = DAY_LABELS[d.getUTCDay()];
      markers.push({
        time: c.time,
        position: "aboveBar",
        color: "#8b8fa3",
        shape: "square",
        size: 0,
        text: `${dayName} ${dd}/${mm}`,
      });
    }
    prevDay = dayKey;
  }
  return markers;
}

function candleStartIST() {
  const nowFakeUtc = Math.floor(Date.now() / 1000) + IST_OFFSET_SEC;
  return nowFakeUtc - (nowFakeUtc % CANDLE_INTERVAL_SEC);
}

export default function SpotChart({ indexId, spotPrice }) {
  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const seriesRef = useRef(null);
  const candleRef = useRef(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [prevClose, setPrevClose] = useState(null);
  const [lastClose, setLastClose] = useState(null);

  const livePrice = spotPrice;

  // Create chart and load historical candles
  useEffect(() => {
    if (!indexId || !containerRef.current) return;

    setLoading(true);
    setError(null);
    setPrevClose(null);
    setLastClose(null);

    const chart = createChart(containerRef.current, {
      autoSize: true,
      layout: {
        background: { color: "#1a1d27" },
        textColor: "#8b8fa3",
        fontFamily:
          '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
      },
      grid: {
        vertLines: { color: "#2e334522" },
        horzLines: { color: "#2e334522" },
      },
      timeScale: {
        timeVisible: true,
        secondsVisible: false,
        borderColor: "#2e3345",
      },
      rightPriceScale: {
        borderColor: "#2e3345",
      },
      crosshair: {
        mode: 0,
      },
    });

    const series = chart.addSeries(CandlestickSeries, {
      upColor: "#22c55e",
      downColor: "#ef4444",
      borderUpColor: "#22c55e",
      borderDownColor: "#ef4444",
      wickUpColor: "#22c55e",
      wickDownColor: "#ef4444",
    });

    chartRef.current = chart;
    seriesRef.current = series;
    candleRef.current = null;

    let cancelled = false;

    fetch(`/api/options/candles/${indexId}`)
      .then((r) => r.json())
      .then((data) => {
        if (cancelled) return;
        if (data.error) {
          setError(data.error);
          setLoading(false);
          return;
        }
        series.setData(data.candles);
        const dayMarkers = buildDayBreakMarkers(data.candles);
        if (dayMarkers.length) series.setMarkers(dayMarkers);
        chart.timeScale().fitContent();
        if (data.candles.length > 0) {
          const last = data.candles[data.candles.length - 1];
          candleRef.current = { ...last };
          setLastClose(last.close);
        }
        if (data.prev_close != null) setPrevClose(data.prev_close);
        setLoading(false);
      })
      .catch((e) => {
        if (cancelled) return;
        setError(e.message);
        setLoading(false);
      });

    return () => {
      cancelled = true;
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
      candleRef.current = null;
    };
  }, [indexId]);

  // Update current candle from live price (prop or WS)
  useEffect(() => {
    if (!seriesRef.current || !livePrice || loading) return;

    const start = candleStartIST();
    const cur = candleRef.current;

    if (!cur || start > cur.time) {
      candleRef.current = {
        time: start,
        open: livePrice,
        high: livePrice,
        low: livePrice,
        close: livePrice,
      };
    } else {
      cur.high = Math.max(cur.high, livePrice);
      cur.low = Math.min(cur.low, livePrice);
      cur.close = livePrice;
    }

    seriesRef.current.update({ ...candleRef.current });
    setLastClose(livePrice);
  }, [livePrice, loading]);

  const currentPrice = livePrice || lastClose;
  const pctChange =
    prevClose && currentPrice
      ? ((currentPrice - prevClose) / prevClose) * 100
      : null;

  const label = DISPLAY_NAMES[indexId] || indexId;

  return (
    <div className="panel chart-panel">
      <div className="panel-header">
        <span>{label} — 15 min</span>
        {pctChange != null && (
          <span
            className="chart-pct"
            style={{ color: pctChange >= 0 ? "var(--green)" : "var(--red)" }}
          >
            {currentPrice.toLocaleString("en-IN", {
              minimumFractionDigits: 2,
              maximumFractionDigits: 2,
            })}{" "}
            ({pctChange >= 0 ? "+" : ""}
            {pctChange.toFixed(2)}%)
          </span>
        )}
      </div>
      <div style={{ position: "relative", height: 280 }}>
        <div ref={containerRef} style={{ width: "100%", height: "100%" }} />
        {loading && <div className="chart-overlay">Loading chart…</div>}
        {error && (
          <div className="chart-overlay chart-overlay-error">{error}</div>
        )}
      </div>
    </div>
  );
}
