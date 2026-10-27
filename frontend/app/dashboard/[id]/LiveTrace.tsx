"use client";
import { useEffect, useState } from "react";

type Ev = {
  graph_run_id?: string;
  seq?: number;
  agent: string;
  kind: string;
  payload: unknown;
};

export function LiveTrace({ id, initial }: { id: string; initial: Ev[] }) {
  const [events, setEvents] = useState<Ev[]>(initial ?? []);

  useEffect(() => {
    let stopped = false;
    let ws: WebSocket | undefined;
    let retry: ReturnType<typeof setTimeout> | undefined;
    async function connect() {
      try {
        const res = await fetch(`/api/rg/returns/${id}/trace-ticket`, {
          method: "POST",
        });
        if (!res.ok || stopped) return;
        const { ticket } = await res.json();
        if (stopped) return;
        const base = process.env.NEXT_PUBLIC_WS_URL ?? "ws://localhost:8000";
        ws = new WebSocket(
          `${base}/ws/returns/${id}?ticket=${encodeURIComponent(ticket)}`,
        );
        ws.onmessage = (m) => {
          try {
            const e = JSON.parse(m.data) as Ev;
            if (e.kind)
              setEvents((cur) =>
                cur.some(
                  (old) =>
                    old.graph_run_id === e.graph_run_id && old.seq === e.seq,
                )
                  ? cur
                  : [...cur, e],
              );
          } catch {
            /* Ignore malformed messages. */
          }
        };
        ws.onclose = () => {
          if (!stopped) retry = setTimeout(connect, 2000);
        };
      } catch {
        if (!stopped) retry = setTimeout(connect, 2000);
      }
    }
    void connect();
    return () => {
      stopped = true;
      clearTimeout(retry);
      ws?.close();
    };
  }, [id]);

  return (
    <div className="d-section" style={{ padding: 10 }}>
      <div className="d-trace">
        {events.map((e, i) => (
          <div key={i} className="d-trace-item">
            <span className={`d-trace-dot ${e.kind}`} />
            <div className="d-trace-body">
              <div className="d-trace-head">
                <strong>{e.agent}</strong>
                <span className="muted">· {e.kind}</span>
              </div>
              <pre>{JSON.stringify(e.payload, null, 1)}</pre>
            </div>
          </div>
        ))}
        {events.length === 0 && (
          <div className="d-empty">waiting for pipeline events…</div>
        )}
      </div>
    </div>
  );
}
