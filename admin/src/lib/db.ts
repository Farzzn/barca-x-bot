import { Pool } from "pg";

export interface BotBehaviorConfig {
  bot_enabled: boolean;
  dry_run: boolean;
  max_posts_per_day: number;
  weekly_post_target: number;
  non_matchday_min_posts: number;
  non_matchday_max_posts: number;
  min_minutes_between_posts: number;
  matchday_cooldown_minutes: number;
  post_hours_utc: number[];
  image_post_ratio: number;
  gemini_model: string;
  enable_google_search: boolean;
  auto_post_halftime: boolean;
  auto_post_fulltime: boolean;
  auto_post_prematch: boolean;
  auto_post_rival_drama: boolean;
  chaos_level: "balanced" | "spicy" | "unhinged";
  enabled_archetypes: string[];
  custom_directive: string;
}

export interface PostHistoryRow {
  id: number;
  tweet_id: string;
  archetype: string;
  match_state: string;
  angle_summary: string;
  text: string;
  image_ids: string[];
  dry_run: boolean;
  trigger_type: string;
  gemini_key_used: string;
  gemini_fallback_reason: string;
  gemini_model: string;
  search_grounded: boolean;
  match_label: string;
  target_club: string;
  workflow_slot: string;
  impressions: number;
  likes: number;
  replies: number;
  retweets: number;
  quotes: number;
  metrics_updated_at: string | null;
  created_at_utc: string;
}

export interface ClubAnalyticsRow {
  club: string;
  livePosts: number;
  dryPosts: number;
  totalImpressions: number;
  avgImpressions: number;
  totalLikes: number;
  totalReplies: number;
  totalRetweets: number;
  totalEngagement: number;
  avgEngagement: number;
  measuredPosts: number;
}

export const ALL_ARCHETYPES = [
  { id: "pre_match_delusion", label: "Pre-Match & Lineup Brag", desc: "8h pre-match & lineup hype" },
  { id: "post_win_hype", label: "HT / FT Win Euphoria", desc: "Unhinged celebration & treble talk" },
  { id: "post_drop_meltdown", label: "HT / FT Meltdown", desc: "Instant rage, 'send Gordon back', 'Flick out'" },
  { id: "rival_comparison_bait", label: "Rival Comparison Bait", desc: "Side-by-side Barça vs rival player wars" },
  { id: "rival_match_slander", label: "Real Madrid & Rival Slander", desc: "Madrid lineup/FT discrediting & rival roasts" },
  { id: "epl_intensity_slander", label: "EPL 'Intensity League' Slander", desc: "Mock Premier League clubs & La Liga > EPL" },
  { id: "would_you_rather", label: "Would You Rather", desc: "2-option Barça transfer & squad dilemmas" },
  { id: "unpopular_one_percent", label: "Unpopular 1% Takes", desc: "Deadpan contrarian Culer hot takes" },
] as const;

export const TRACKED_CLUBS = [
  "FC Barcelona",
  "Real Madrid",
  "Arsenal",
  "Manchester City",
  "Liverpool",
  "Chelsea",
  "Manchester United",
  "Atletico Madrid",
  "PSG",
  "Bayern Munich",
  "EPL (Intensity League)",
] as const;

export const DEFAULT_BEHAVIOR: BotBehaviorConfig = {
  bot_enabled: true,
  dry_run: false,
  max_posts_per_day: 6,
  weekly_post_target: 26,
  non_matchday_min_posts: 1,
  non_matchday_max_posts: 3,
  min_minutes_between_posts: 150,
  matchday_cooldown_minutes: 20,
  post_hours_utc: [6, 11, 16],
  image_post_ratio: 0.6,
  gemini_model: "gemini-3.5-flash-lite",
  enable_google_search: false,
  auto_post_halftime: true,
  auto_post_fulltime: true,
  auto_post_prematch: true,
  auto_post_rival_drama: true,
  chaos_level: "spicy",
  enabled_archetypes: ALL_ARCHETYPES.map((a) => a.id),
  custom_directive: "",
};

const globalForPg = globalThis as unknown as { barcaPool?: Pool; barcaSchemaReady?: boolean };

export function isDatabaseConfigured(): boolean {
  return Boolean(process.env.DATABASE_URL?.trim());
}

function getPool(): Pool {
  if (!globalForPg.barcaPool) {
    const rawUrl = process.env.DATABASE_URL?.trim() || "";
    const connectionString = rawUrl.startsWith("postgres://")
      ? `postgresql://${rawUrl.slice("postgres://".length)}`
      : rawUrl;
    globalForPg.barcaPool = new Pool({
      connectionString,
      ssl: connectionString.includes("localhost") ? undefined : { rejectUnauthorized: false },
      max: 5,
    });
  }
  return globalForPg.barcaPool;
}

export async function ensureSchema(): Promise<void> {
  if (!isDatabaseConfigured() || globalForPg.barcaSchemaReady) return;
  const pool = getPool();
  await pool.query(`
    CREATE TABLE IF NOT EXISTS x_post_history (
      id SERIAL PRIMARY KEY,
      tweet_id TEXT,
      archetype TEXT NOT NULL,
      match_state TEXT NOT NULL,
      angle_summary TEXT,
      text TEXT NOT NULL,
      image_ids TEXT NOT NULL,
      dry_run BOOLEAN NOT NULL DEFAULT FALSE,
      trigger_type TEXT NOT NULL DEFAULT 'scheduled',
      gemini_key_used TEXT NOT NULL DEFAULT 'primary',
      gemini_fallback_reason TEXT NOT NULL DEFAULT '',
      gemini_model TEXT NOT NULL DEFAULT '',
      search_grounded BOOLEAN NOT NULL DEFAULT FALSE,
      match_label TEXT NOT NULL DEFAULT '',
      target_club TEXT NOT NULL DEFAULT 'FC Barcelona',
      workflow_slot TEXT NOT NULL DEFAULT 'scheduled',
      impressions INTEGER NOT NULL DEFAULT 0,
      likes INTEGER NOT NULL DEFAULT 0,
      replies INTEGER NOT NULL DEFAULT 0,
      retweets INTEGER NOT NULL DEFAULT 0,
      quotes INTEGER NOT NULL DEFAULT 0,
      metrics_updated_at TIMESTAMPTZ,
      created_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
  `);
  await pool.query(`
    CREATE TABLE IF NOT EXISTS bot_settings (
      id INTEGER PRIMARY KEY,
      config_json TEXT NOT NULL,
      updated_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
  `);
  await pool.query(`
    CREATE TABLE IF NOT EXISTS match_events_sent (
      match_key TEXT NOT NULL,
      event_type TEXT NOT NULL,
      created_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      PRIMARY KEY (match_key, event_type)
    );
  `);
  // Idempotent migrations
  await pool.query(`ALTER TABLE x_post_history ADD COLUMN IF NOT EXISTS trigger_type TEXT NOT NULL DEFAULT 'scheduled'`);
  await pool.query(`ALTER TABLE x_post_history ADD COLUMN IF NOT EXISTS gemini_key_used TEXT NOT NULL DEFAULT 'primary'`);
  await pool.query(`ALTER TABLE x_post_history ADD COLUMN IF NOT EXISTS gemini_fallback_reason TEXT NOT NULL DEFAULT ''`);
  await pool.query(`ALTER TABLE x_post_history ADD COLUMN IF NOT EXISTS gemini_model TEXT NOT NULL DEFAULT ''`);
  await pool.query(`ALTER TABLE x_post_history ADD COLUMN IF NOT EXISTS search_grounded BOOLEAN NOT NULL DEFAULT FALSE`);
  await pool.query(`ALTER TABLE x_post_history ADD COLUMN IF NOT EXISTS match_label TEXT NOT NULL DEFAULT ''`);
  await pool.query(`ALTER TABLE x_post_history ADD COLUMN IF NOT EXISTS target_club TEXT NOT NULL DEFAULT 'FC Barcelona'`);
  await pool.query(`ALTER TABLE x_post_history ADD COLUMN IF NOT EXISTS workflow_slot TEXT NOT NULL DEFAULT 'scheduled'`);
  await pool.query(`ALTER TABLE x_post_history ADD COLUMN IF NOT EXISTS impressions INTEGER NOT NULL DEFAULT 0`);
  await pool.query(`ALTER TABLE x_post_history ADD COLUMN IF NOT EXISTS likes INTEGER NOT NULL DEFAULT 0`);
  await pool.query(`ALTER TABLE x_post_history ADD COLUMN IF NOT EXISTS replies INTEGER NOT NULL DEFAULT 0`);
  await pool.query(`ALTER TABLE x_post_history ADD COLUMN IF NOT EXISTS retweets INTEGER NOT NULL DEFAULT 0`);
  await pool.query(`ALTER TABLE x_post_history ADD COLUMN IF NOT EXISTS quotes INTEGER NOT NULL DEFAULT 0`);
  await pool.query(`ALTER TABLE x_post_history ADD COLUMN IF NOT EXISTS metrics_updated_at TIMESTAMPTZ`);

  globalForPg.barcaSchemaReady = true;
}

export function sanitizeBehaviorConfig(input: Partial<BotBehaviorConfig>): BotBehaviorConfig {
  const hoursRaw = Array.isArray(input.post_hours_utc) ? input.post_hours_utc : DEFAULT_BEHAVIOR.post_hours_utc;
  const postHours = Array.from(
    new Set(hoursRaw.map((h) => Number(h)).filter((h) => Number.isInteger(h) && h >= 0 && h <= 23))
  ).sort((a, b) => a - b);

  const validArchSet = new Set<string>(ALL_ARCHETYPES.map((a) => a.id));
  const enabledArch = Array.isArray(input.enabled_archetypes)
    ? input.enabled_archetypes.filter((a) => validArchSet.has(String(a)))
    : DEFAULT_BEHAVIOR.enabled_archetypes;

  const chaos =
    input.chaos_level === "balanced" || input.chaos_level === "spicy" || input.chaos_level === "unhinged"
      ? input.chaos_level
      : DEFAULT_BEHAVIOR.chaos_level;

  const nonMin = Math.min(5, Math.max(0, Number(input.non_matchday_min_posts ?? DEFAULT_BEHAVIOR.non_matchday_min_posts)));
  const nonMax = Math.min(6, Math.max(nonMin || 1, Number(input.non_matchday_max_posts ?? DEFAULT_BEHAVIOR.non_matchday_max_posts)));

  return {
    bot_enabled: typeof input.bot_enabled === "boolean" ? input.bot_enabled : DEFAULT_BEHAVIOR.bot_enabled,
    dry_run: typeof input.dry_run === "boolean" ? input.dry_run : DEFAULT_BEHAVIOR.dry_run,
    max_posts_per_day: Math.min(15, Math.max(1, Number(input.max_posts_per_day ?? DEFAULT_BEHAVIOR.max_posts_per_day) || 6)),
    weekly_post_target: Math.min(70, Math.max(7, Number(input.weekly_post_target ?? DEFAULT_BEHAVIOR.weekly_post_target) || 26)),
    non_matchday_min_posts: nonMin,
    non_matchday_max_posts: nonMax,
    min_minutes_between_posts: Math.min(
      720,
      Math.max(15, Number(input.min_minutes_between_posts ?? DEFAULT_BEHAVIOR.min_minutes_between_posts) || 150)
    ),
    matchday_cooldown_minutes: Math.min(
      240,
      Math.max(10, Number(input.matchday_cooldown_minutes ?? DEFAULT_BEHAVIOR.matchday_cooldown_minutes) || 20)
    ),
    post_hours_utc: postHours.length > 0 ? postHours : DEFAULT_BEHAVIOR.post_hours_utc,
    image_post_ratio: Math.min(1, Math.max(0, Number(input.image_post_ratio ?? DEFAULT_BEHAVIOR.image_post_ratio))),
    gemini_model: String(input.gemini_model || DEFAULT_BEHAVIOR.gemini_model).trim(),
    enable_google_search:
      typeof input.enable_google_search === "boolean"
        ? input.enable_google_search
        : DEFAULT_BEHAVIOR.enable_google_search,
    auto_post_halftime:
      typeof input.auto_post_halftime === "boolean" ? input.auto_post_halftime : DEFAULT_BEHAVIOR.auto_post_halftime,
    auto_post_fulltime:
      typeof input.auto_post_fulltime === "boolean" ? input.auto_post_fulltime : DEFAULT_BEHAVIOR.auto_post_fulltime,
    auto_post_prematch:
      typeof input.auto_post_prematch === "boolean" ? input.auto_post_prematch : DEFAULT_BEHAVIOR.auto_post_prematch,
    auto_post_rival_drama:
      typeof input.auto_post_rival_drama === "boolean"
        ? input.auto_post_rival_drama
        : DEFAULT_BEHAVIOR.auto_post_rival_drama,
    chaos_level: chaos,
    enabled_archetypes: enabledArch.length > 0 ? enabledArch : DEFAULT_BEHAVIOR.enabled_archetypes,
    custom_directive: String(input.custom_directive ?? "").slice(0, 500),
  };
}

export async function getBehaviorConfig(): Promise<{ config: BotBehaviorConfig; updatedAt: string | null }> {
  if (!isDatabaseConfigured()) {
    return { config: DEFAULT_BEHAVIOR, updatedAt: null };
  }
  await ensureSchema();
  const pool = getPool();
  const res = await pool.query("SELECT config_json, updated_at_utc FROM bot_settings WHERE id = 1");
  if (res.rows.length === 0) {
    await saveBehaviorConfig(DEFAULT_BEHAVIOR);
    return { config: DEFAULT_BEHAVIOR, updatedAt: new Date().toISOString() };
  }
  const parsed = JSON.parse(res.rows[0].config_json) as Partial<BotBehaviorConfig>;
  return {
    config: sanitizeBehaviorConfig(parsed),
    updatedAt: res.rows[0].updated_at_utc ? new Date(res.rows[0].updated_at_utc).toISOString() : null,
  };
}

export async function saveBehaviorConfig(config: BotBehaviorConfig): Promise<BotBehaviorConfig> {
  const clean = sanitizeBehaviorConfig(config);
  if (!isDatabaseConfigured()) {
    throw new Error("DATABASE_URL is not configured.");
  }
  await ensureSchema();
  const pool = getPool();
  await pool.query(
    `
    INSERT INTO bot_settings (id, config_json, updated_at_utc)
    VALUES (1, $1, NOW())
    ON CONFLICT (id) DO UPDATE
    SET config_json = EXCLUDED.config_json, updated_at_utc = NOW()
    `,
    [JSON.stringify(clean)]
  );
  return clean;
}

export async function updatePostMetrics(
  postId: number,
  metrics: { impressions: number; likes: number; replies: number; retweets: number; quotes: number }
): Promise<void> {
  if (!isDatabaseConfigured()) {
    throw new Error("DATABASE_URL is not configured.");
  }
  await ensureSchema();
  const pool = getPool();
  await pool.query(
    `
    UPDATE x_post_history
    SET impressions = $2, likes = $3, replies = $4, retweets = $5, quotes = $6, metrics_updated_at = NOW()
    WHERE id = $1
    `,
    [
      postId,
      Math.max(0, Math.floor(metrics.impressions || 0)),
      Math.max(0, Math.floor(metrics.likes || 0)),
      Math.max(0, Math.floor(metrics.replies || 0)),
      Math.max(0, Math.floor(metrics.retweets || 0)),
      Math.max(0, Math.floor(metrics.quotes || 0)),
    ]
  );
}

export async function getActivityData(limit = 100): Promise<{
  posts: PostHistoryRow[];
  clubAnalytics: ClubAnalyticsRow[];
  stats: {
    postsTodayLive: number;
    postsLast7Days: number;
    dailyAvg7d: number;
    totalLivePosts: number;
    totalDryRuns: number;
    primaryKeyCount: number;
    fallbackKeyCount: number;
    halftimeCount: number;
    fulltimeCount: number;
    estimatedXSpendUsd: number;
  };
}> {
  if (!isDatabaseConfigured()) {
    return {
      posts: [],
      clubAnalytics: [],
      stats: {
        postsTodayLive: 0,
        postsLast7Days: 0,
        dailyAvg7d: 0,
        totalLivePosts: 0,
        totalDryRuns: 0,
        primaryKeyCount: 0,
        fallbackKeyCount: 0,
        halftimeCount: 0,
        fulltimeCount: 0,
        estimatedXSpendUsd: 0,
      },
    };
  }
  await ensureSchema();
  const pool = getPool();

  const [rowsRes, aggRes, clubRes] = await Promise.all([
    pool.query(
      `
      SELECT id, tweet_id, archetype, match_state, angle_summary, text, image_ids,
             dry_run, trigger_type, gemini_key_used, gemini_fallback_reason,
             gemini_model, search_grounded, match_label, target_club, workflow_slot,
             impressions, likes, replies, retweets, quotes, metrics_updated_at, created_at_utc
      FROM x_post_history
      ORDER BY id DESC
      LIMIT $1
      `,
      [limit]
    ),
    pool.query(`
      SELECT
        COUNT(*) FILTER (WHERE dry_run = FALSE AND created_at_utc >= DATE_TRUNC('day', NOW() AT TIME ZONE 'UTC')) AS posts_today_live,
        COUNT(*) FILTER (WHERE dry_run = FALSE AND created_at_utc >= NOW() - INTERVAL '7 days') AS posts_last_7d,
        COUNT(*) FILTER (WHERE dry_run = FALSE) AS total_live,
        COUNT(*) FILTER (WHERE dry_run = TRUE) AS total_dry,
        COUNT(*) FILTER (WHERE gemini_key_used = 'primary') AS primary_key_count,
        COUNT(*) FILTER (WHERE gemini_key_used = 'fallback') AS fallback_key_count,
        COUNT(*) FILTER (WHERE trigger_type = 'halftime') AS halftime_count,
        COUNT(*) FILTER (WHERE trigger_type = 'fulltime') AS fulltime_count
      FROM x_post_history
    `),
    pool.query(`
      SELECT
        COALESCE(NULLIF(target_club, ''), 'FC Barcelona') AS club,
        COUNT(*) FILTER (WHERE dry_run = FALSE) AS live_posts,
        COUNT(*) FILTER (WHERE dry_run = TRUE) AS dry_posts,
        COALESCE(SUM(impressions) FILTER (WHERE dry_run = FALSE), 0) AS total_impressions,
        COALESCE(SUM(likes) FILTER (WHERE dry_run = FALSE), 0) AS total_likes,
        COALESCE(SUM(replies) FILTER (WHERE dry_run = FALSE), 0) AS total_replies,
        COALESCE(SUM(retweets + quotes) FILTER (WHERE dry_run = FALSE), 0) AS total_retweets,
        COUNT(*) FILTER (WHERE dry_run = FALSE AND (impressions > 0 OR likes > 0 OR replies > 0)) AS measured_posts
      FROM x_post_history
      GROUP BY 1
      ORDER BY total_impressions DESC, live_posts DESC, dry_posts DESC
    `),
  ]);

  const posts: PostHistoryRow[] = rowsRes.rows.map((r) => {
    let imageIds: string[] = [];
    try {
      imageIds = JSON.parse(r.image_ids || "[]");
    } catch {
      imageIds = [];
    }
    return {
      id: Number(r.id),
      tweet_id: r.tweet_id || "",
      archetype: r.archetype,
      match_state: r.match_state,
      angle_summary: r.angle_summary || "",
      text: r.text,
      image_ids: imageIds,
      dry_run: Boolean(r.dry_run),
      trigger_type: r.trigger_type || "scheduled",
      gemini_key_used: r.gemini_key_used || "primary",
      gemini_fallback_reason: r.gemini_fallback_reason || "",
      gemini_model: r.gemini_model || "",
      search_grounded: Boolean(r.search_grounded),
      match_label: r.match_label || "",
      target_club: r.target_club || "FC Barcelona",
      workflow_slot: r.workflow_slot || "scheduled",
      impressions: Number(r.impressions || 0),
      likes: Number(r.likes || 0),
      replies: Number(r.replies || 0),
      retweets: Number(r.retweets || 0),
      quotes: Number(r.quotes || 0),
      metrics_updated_at: r.metrics_updated_at ? new Date(r.metrics_updated_at).toISOString() : null,
      created_at_utc: new Date(r.created_at_utc).toISOString(),
    };
  });

  const clubAnalytics: ClubAnalyticsRow[] = clubRes.rows.map((r) => {
    const livePosts = Number(r.live_posts || 0);
    const measured = Number(r.measured_posts || 0);
    const totalImp = Number(r.total_impressions || 0);
    const totalLikes = Number(r.total_likes || 0);
    const totalReplies = Number(r.total_replies || 0);
    const totalRetweets = Number(r.total_retweets || 0);
    const totalEng = totalLikes + totalReplies + totalRetweets;
    const denom = measured > 0 ? measured : livePosts || 1;
    return {
      club: String(r.club),
      livePosts,
      dryPosts: Number(r.dry_posts || 0),
      totalImpressions: totalImp,
      avgImpressions: measured > 0 ? Math.round(totalImp / denom) : 0,
      totalLikes,
      totalReplies,
      totalRetweets,
      totalEngagement: totalEng,
      avgEngagement: measured > 0 ? Number((totalEng / denom).toFixed(1)) : 0,
      measuredPosts: measured,
    };
  });

  const a = aggRes.rows[0] || {};
  const totalLive = Number(a.total_live || 0);
  const postsLast7d = Number(a.posts_last_7d || 0);
  return {
    posts,
    clubAnalytics,
    stats: {
      postsTodayLive: Number(a.posts_today_live || 0),
      postsLast7Days: postsLast7d,
      dailyAvg7d: Number((postsLast7d / 7).toFixed(2)),
      totalLivePosts: totalLive,
      totalDryRuns: Number(a.total_dry || 0),
      primaryKeyCount: Number(a.primary_key_count || 0),
      fallbackKeyCount: Number(a.fallback_key_count || 0),
      halftimeCount: Number(a.halftime_count || 0),
      fulltimeCount: Number(a.fulltime_count || 0),
      estimatedXSpendUsd: Number((totalLive * 0.015).toFixed(3)),
    },
  };
}

export async function insertPostRecord(
  record: Omit<PostHistoryRow, "id" | "created_at_utc" | "impressions" | "likes" | "replies" | "retweets" | "quotes" | "metrics_updated_at">
): Promise<void> {
  if (!isDatabaseConfigured()) return;
  await ensureSchema();
  const pool = getPool();
  await pool.query(
    `
    INSERT INTO x_post_history (
      tweet_id, archetype, match_state, angle_summary, text, image_ids,
      dry_run, trigger_type, gemini_key_used, gemini_fallback_reason,
      gemini_model, search_grounded, match_label, target_club, workflow_slot, created_at_utc
    )
    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,NOW())
    `,
    [
      record.tweet_id,
      record.archetype,
      record.match_state,
      record.angle_summary,
      record.text,
      JSON.stringify(record.image_ids),
      record.dry_run,
      record.trigger_type,
      record.gemini_key_used,
      record.gemini_fallback_reason,
      record.gemini_model,
      record.search_grounded,
      record.match_label,
      record.target_club,
      record.workflow_slot,
    ]
  );
}
