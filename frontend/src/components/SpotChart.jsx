import { useEffect, useRef, useState } from "react";
import { createChart, CandlestickSeries } from "lightweight-charts";

const IST_OFFSET_SEC = 5.5 * 3600;
const CANDLE_INTERVAL_SEC = 15 * 60;

const DISPLAY_NAMES = {
  NIFTY: "NIFTY 50",
  SENSEX: "SENSEX",
  INDIAVIX: "INDIA VIX",
  GIFTNIFTY: "GIFT NIFTY",
};

function candleStartIST() {
  const nowFakeUtc = Math.floor(Date.now() / 1000) + IST_OFFSET_SEC;
  return nowFakeUtc - (nowFakeUtc % CANDLE_INTERVAL_SEC);
}

export default function SpotChart({ indexId, spotPrice, liveEndpoint }) {
  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const seriesRef = useRef(null);
  const candleRef = useRef(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [prevClose, setPrevClose] = useState(null);
  const [lastClose, setLastClose] = useState(null);
  const [wsPrice, setWsPrice] = useState(null);

  // Optional: connect to a backend WS for live prices (Kite ticker)
  useEffect(() => {
    if (!liveEndpoint || !indexId) return;
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${proto}//${window.location.host}${liveEndpoint}`;
    const ws = new WebSocket(url);
    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      if (msg.type === "tick" && msg.prices[indexId] != null) {
        setWsPrice(msg.prices[indexId]);
      }
    };
    ws.onerror = () => {};
    return () => ws.close();
  }, [liveEndpoint, indexId]);

  const livePrice = spotPrice || wsPrice;

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
