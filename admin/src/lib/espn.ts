export interface LiveMatchSnapshot {
  competition: string;
  homeTeam: string;
  awayTeam: string;
  homeScore: string | null;
  awayScore: string | null;
  state: string;
  detail: string;
  kickoffUtc: string;
  involvesBarca: boolean;
}

const LEAGUES = [
  { slug: "esp.1", name: "La Liga" },
  { slug: "uefa.champions", name: "UCL" },
  { slug: "esp.copa_del_rey", name: "Copa del Rey" },
];

function formatDateYmd(d: Date): string {
  const y = d.getUTCFullYear();
  const m = String(d.getUTCMonth() + 1).padStart(2, "0");
  const day = String(d.getUTCDate()).padStart(2, "0");
  return `${y}${m}${day}`;
}

export async function fetchBarcaSnapshot(): Promise<{
  barcaMatches: LiveMatchSnapshot[];
  rivalMatches: LiveMatchSnapshot[];
}> {
  const now = new Date();
  const days = [
    formatDateYmd(new Date(now.getTime() - 86400_000)),
    formatDateYmd(now),
    formatDateYmd(new Date(now.getTime() + 86400_000)),
  ];

  const barcaMap = new Map<string, LiveMatchSnapshot>();
  const rivalMap = new Map<string, LiveMatchSnapshot>();
  const rivals = ["real madrid", "atletico", "atlético", "manchester city", "arsenal", "psg", "bayern"];

  await Promise.all(
    LEAGUES.flatMap((lg) =>
      days.map(async (day) => {
        try {
          const res = await fetch(
            `https://site.api.espn.com/apis/site/v2/sports/soccer/${lg.slug}/scoreboard?dates=${day}`,
            { next: { revalidate: 120 } }
          );
          if (!res.ok) return;
          const data = (await res.json()) as {
            events?: Array<{
              id?: string;
              date?: string;
              competitions?: Array<{
                date?: string;
                status?: { type?: { state?: string; shortDetail?: string; description?: string } };
                competitors?: Array<{ homeAway?: string; score?: string; team?: { displayName?: string } }>;
              }>;
            }>;
          };
          for (const ev of data.events || []) {
            const comp = ev.competitions?.[0];
            if (!comp) continue;
            const home = comp.competitors?.find((c) => c.homeAway === "home");
            const away = comp.competitors?.find((c) => c.homeAway === "away");
            if (!home || !away) continue;
            const homeTeam = home.team?.displayName || "Unknown";
            const awayTeam = away.team?.displayName || "Unknown";
            const combined = `${homeTeam} ${awayTeam}`.toLowerCase();
            const isBarca = combined.includes("barcelona");
            const isRival = !isBarca && rivals.some((r) => combined.includes(r));
            if (!isBarca && !isRival) continue;

            const state = (comp.status?.type?.state || "pre").toLowerCase();
            const snap: LiveMatchSnapshot = {
              competition: lg.name,
              homeTeam,
              awayTeam,
              homeScore: state === "pre" ? null : home.score ?? null,
              awayScore: state === "pre" ? null : away.score ?? null,
              state,
              detail: comp.status?.type?.shortDetail || comp.status?.type?.description || state,
              kickoffUtc: comp.date || ev.date || "",
              involvesBarca: isBarca,
            };
            const key = `${lg.slug}:${ev.id || `${homeTeam}_${awayTeam}`}`;
            if (isBarca) barcaMap.set(key, snap);
            else rivalMap.set(key, snap);
          }
        } catch {
          // Ignore individual league errors
        }
      })
    )
  );

  return {
    barcaMatches: Array.from(barcaMap.values()).sort((a, b) => a.kickoffUtc.localeCompare(b.kickoffUtc)),
    rivalMatches: Array.from(rivalMap.values()).sort((a, b) => a.kickoffUtc.localeCompare(b.kickoffUtc)),
  };
}
