"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  ExternalLink,
  Flame,
  Image as ImageIcon,
  KeyRound,
  Loader2,
  Lock,
  LogOut,
  PauseCircle,
  PlayCircle,
  RefreshCw,
  Save,
  Sliders,
  Sparkles,
  Trash2,
  Upload,
} from "lucide-react";
import type { BotBehaviorConfig, PostHistoryRow } from "@/lib/db";
import type { LiveMatchSnapshot } from "@/lib/espn";
import type { CatalogImage } from "@/lib/github";

type TabId = "activity" | "controls" | "images";

interface AuthStatus {
  authenticated: boolean;
  authRequired: boolean;
  databaseConfigured: boolean;
  githubConfigured: boolean;
  geminiPreviewReady: boolean;
}

interface ActivityStats {
  postsTodayLive: number;
  totalLivePosts: number;
  totalDryRuns: number;
  primaryKeyCount: number;
  fallbackKeyCount: number;
  halftimeCount: number;
  fulltimeCount: number;
  estimatedXSpendUsd: number;
}

interface PreviewResult {
  text: string;
  selected_image_ids: string[];
  angle_summary: string;
  archetype: string;
  gemini_key_used: "primary" | "fallback";
  gemini_fallback_reason: string;
  gemini_model: string;
}

const ARCHETYPES = [
  { id: "pre_match_delusion", label: "Pre-Match Delusion", desc: "Overconfident lineup & scoreline hype" },
  { id: "post_win_hype", label: "Post-Win / HT Euphoria", desc: "Unhinged celebration & treble talk" },
  { id: "post_drop_meltdown", label: "Draw/Loss Meltdown", desc: "Instant rage, 'tactically empty', 'sack him'" },
  { id: "rival_comparison_bait", label: "Rival Comparison Bait", desc: "Side-by-side Barça vs Madrid player civil wars" },
  { id: "rival_match_slander", label: "Rival Match Slander", desc: "Madrid/UCL jinxes, haram ball & ref banter" },
  { id: "epl_intensity_slander", label: "EPL 'Intensity League' Slander", desc: "Mock Premier League clubs & push La Liga > EPL" },
  { id: "would_you_rather", label: "Would You Rather", desc: "2-option Barça transfer & squad dilemmas" },
  { id: "unpopular_one_percent", label: "Unpopular 1% Takes", desc: "Deadpan contrarian Culer hot takes" },
];

const DEFAULT_CONFIG: BotBehaviorConfig = {
  bot_enabled: true,
  dry_run: false,
  max_posts_per_day: 6,
  min_minutes_between_posts: 110,
  matchday_cooldown_minutes: 45,
  post_hours_utc: [8, 11, 14, 17, 20, 22],
  image_post_ratio: 0.55,
  gemini_model: "gemini-3.5-flash-lite",
  enable_google_search: false,
  auto_post_halftime: true,
  auto_post_fulltime: true,
  auto_post_prematch: true,
  auto_post_rival_drama: true,
  chaos_level: "spicy",
  enabled_archetypes: ARCHETYPES.map((a) => a.id),
  custom_directive: "",
};

const MOOD_PRESETS = [
  "celebrating",
  "cold",
  "smiling",
  "serious",
  "angry",
  "frustrated",
  "complaining",
  "shocked",
  "sad",
  "crying",
  "arms_up",
];

function formatRelativeTime(iso: string): string {
  const diffMs = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diffMs / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

export default function AdminDashboard() {
  const [auth, setAuth] = useState<AuthStatus | null>(null);
  const [password, setPassword] = useState("");
  const [authError, setAuthError] = useState("");
  const [loggingIn, setLoggingIn] = useState(false);

  const [tab, setTab] = useState<TabId>("activity");
  const [loading, setLoading] = useState(false);
  const [banner, setBanner] = useState<{ type: "ok" | "err"; text: string } | null>(null);

  // Settings state
  const [config, setConfig] = useState<BotBehaviorConfig>(DEFAULT_CONFIG);
  const [savingConfig, setSavingConfig] = useState(false);
  const [settingsUpdatedAt, setSettingsUpdatedAt] = useState<string | null>(null);

  // Activity state
  const [posts, setPosts] = useState<PostHistoryRow[]>([]);
  const [stats, setStats] = useState<ActivityStats>({
    postsTodayLive: 0,
    totalLivePosts: 0,
    totalDryRuns: 0,
    primaryKeyCount: 0,
    fallbackKeyCount: 0,
    halftimeCount: 0,
    fulltimeCount: 0,
    estimatedXSpendUsd: 0,
  });
  const [barcaMatches, setBarcaMatches] = useState<LiveMatchSnapshot[]>([]);
  const [rivalMatches, setRivalMatches] = useState<LiveMatchSnapshot[]>([]);
  const [activityFilter, setActivityFilter] = useState<"all" | "live" | "dry" | "fallback" | "match">("all");

  // Preview generator state
  const [previewArchetype, setPreviewArchetype] = useState("rival_comparison_bait");
  const [previewTrigger, setPreviewTrigger] = useState("manual");
  const [generatingPreview, setGeneratingPreview] = useState(false);
  const [previewResult, setPreviewResult] = useState<PreviewResult | null>(null);

  // GitHub Images state
  const [images, setImages] = useState<CatalogImage[]>([]);
  const [githubMeta, setGithubMeta] = useState<{ repo: string; branch: string; configured: boolean }>({
    repo: "",
    branch: "main",
    configured: false,
  });
  const [imageFilterCat, setImageFilterCat] = useState<string>("all");
  const [uploading, setUploading] = useState(false);
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploadCategory, setUploadCategory] = useState<"barca" | "rivals" | "managers" | "memes">("barca");
  const [uploadSubject, setUploadSubject] = useState("");
  const [uploadClub, setUploadClub] = useState("FC Barcelona");
  const [uploadMood, setUploadMood] = useState("celebrating");
  const [uploadDescription, setUploadDescription] = useState("");
  const [deletingPath, setDeletingPath] = useState<string | null>(null);

  const flash = useCallback((type: "ok" | "err", text: string) => {
    setBanner({ type, text });
    setTimeout(() => setBanner((curr) => (curr?.text === text ? null : curr)), 5000);
  }, []);

  const loadAll = useCallback(async () => {
    setLoading(true);
    try {
      const [settingsRes, activityRes, imagesRes] = await Promise.all([
        fetch("/api/settings", { cache: "no-store" }),
        fetch("/api/activity?limit=80", { cache: "no-store" }),
        fetch("/api/images", { cache: "no-store" }),
      ]);

      if (settingsRes.ok) {
        const sData = await settingsRes.json();
        setConfig(sData.config || DEFAULT_CONFIG);
        setSettingsUpdatedAt(sData.updatedAt || null);
      }
      if (activityRes.ok) {
        const aData = await activityRes.json();
        setPosts(aData.posts || []);
        if (aData.stats) setStats(aData.stats);
        setBarcaMatches(aData.liveMatches?.barcaMatches || []);
        setRivalMatches(aData.liveMatches?.rivalMatches || []);
      }
      if (imagesRes.ok) {
        const iData = await imagesRes.json();
        setImages(iData.images || []);
        setGithubMeta({
          repo: iData.repo || "",
          branch: iData.branch || "main",
          configured: Boolean(iData.githubConfigured),
        });
      }
    } catch (err) {
      flash("err", err instanceof Error ? err.message : "Failed to load dashboard data");
    } finally {
      setLoading(false);
    }
  }, [flash]);

  useEffect(() => {
    fetch("/api/auth/me", { cache: "no-store" })
      .then((r) => r.json())
      .then((data: AuthStatus) => {
        setAuth(data);
        if (data.authenticated) {
          void loadAll();
        }
      })
      .catch(() => {
        setAuth({
          authenticated: false,
          authRequired: true,
          databaseConfigured: false,
          githubConfigured: false,
          geminiPreviewReady: false,
        });
      });
  }, [loadAll]);

  async function handleLogin(e: React.FormEvent) {
    e.preventDefault();
    setLoggingIn(true);
    setAuthError("");
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password }),
      });
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        setAuthError(d.error || "Login failed");
        return;
      }
      setPassword("");
      const me = await fetch("/api/auth/me").then((r) => r.json());
      setAuth(me);
      await loadAll();
    } finally {
      setLoggingIn(false);
    }
  }

  async function handleLogout() {
    await fetch("/api/auth/logout", { method: "POST" });
    setAuth((prev) => (prev ? { ...prev, authenticated: false } : null));
  }

  async function handleSaveConfig(nextConfig: BotBehaviorConfig = config) {
    setSavingConfig(true);
    try {
      const res = await fetch("/api/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(nextConfig),
      });
      const data = await res.json();
      if (!res.ok) {
        flash("err", data.error || "Failed to save settings");
        return;
      }
      setConfig(data.config);
      setSettingsUpdatedAt(data.updatedAt);
      flash("ok", "Behavior settings saved live — Heroku will use them on the very next tick.");
    } catch (err) {
      flash("err", err instanceof Error ? err.message : "Save failed");
    } finally {
      setSavingConfig(false);
    }
  }

  async function handleQuickToggleBot() {
    const updated = { ...config, bot_enabled: !config.bot_enabled };
    setConfig(updated);
    await handleSaveConfig(updated);
  }

  async function handleGeneratePreview() {
    setGeneratingPreview(true);
    setPreviewResult(null);
    try {
      const res = await fetch("/api/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ archetype: previewArchetype, trigger_type: previewTrigger }),
      });
      const data = await res.json();
      if (!res.ok) {
        flash("err", data.error || "Preview generation failed");
        return;
      }
      setPreviewResult(data.preview);
      flash("ok", `Preview generated via Gemini ${data.preview.gemini_key_used.toUpperCase()} key!`);
      await loadAll();
    } catch (err) {
      flash("err", err instanceof Error ? err.message : "Preview error");
    } finally {
      setGeneratingPreview(false);
    }
  }

  async function handleUploadImage(e: React.FormEvent) {
    e.preventDefault();
    if (!uploadFile || !uploadSubject.trim()) {
      flash("err", "Choose an image file and enter the player/subject name.");
      return;
    }
    setUploading(true);
    try {
      const fd = new FormData();
      fd.append("file", uploadFile);
      fd.append("category", uploadCategory);
      fd.append("subject", uploadSubject.trim());
      fd.append("club", uploadClub.trim());
      fd.append("mood", uploadMood.trim());
      fd.append("description", uploadDescription.trim());

      const res = await fetch("/api/images/upload", { method: "POST", body: fd });
      const data = await res.json();
      if (!res.ok) {
        flash("err", data.error || "Upload failed");
        return;
      }
      flash("ok", `Uploaded ${data.image.relative_path} to GitHub & synced catalog.json!`);
      setUploadFile(null);
      setUploadSubject("");
      setUploadDescription("");
      await loadAll();
    } catch (err) {
      flash("err", err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploading(false);
    }
  }

  async function handleDeleteImage(relativePath: string) {
    setDeletingPath(relativePath);
    try {
      const res = await fetch("/api/images", {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ relative_path: relativePath }),
      });
      const data = await res.json();
      if (!res.ok) {
        flash("err", data.error || "Delete failed");
        return;
      }
      flash("ok", `Deleted ${relativePath} from GitHub.`);
      await loadAll();
    } finally {
      setDeletingPath(null);
    }
  }

  const filteredPosts = useMemo(() => {
    return posts.filter((p) => {
      if (activityFilter === "live") return !p.dry_run;
      if (activityFilter === "dry") return p.dry_run;
      if (activityFilter === "fallback") return p.gemini_key_used === "fallback";
      if (activityFilter === "match") return p.trigger_type === "halftime" || p.trigger_type === "fulltime";
      return true;
    });
  }, [posts, activityFilter]);

  const filteredImages = useMemo(() => {
    if (imageFilterCat === "all") return images;
    return images.filter((img) => img.category === imageFilterCat);
  }, [images, imageFilterCat]);

  const totalKeyCalls = stats.primaryKeyCount + stats.fallbackKeyCount;
  const primaryPct = totalKeyCalls > 0 ? Math.round((stats.primaryKeyCount / totalKeyCalls) * 100) : 100;

  // Loading auth gate
  if (!auth) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Loader2 className="h-8 w-8 animate-spin text-[#edbb00]" />
      </div>
    );
  }

  // Login screen
  if (!auth.authenticated) {
    return (
      <div className="flex min-h-screen items-center justify-center px-4">
        <form
          onSubmit={handleLogin}
          className="w-full max-w-md rounded-2xl border border-slate-800 bg-slate-900/90 p-8 shadow-2xl backdrop-blur"
        >
          <div className="mb-6 flex items-center gap-3">
            <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-gradient-to-br from-[#004d98] to-[#a50044] shadow-lg">
              <Lock className="h-5 w-5 text-white" />
            </div>
            <div>
              <h1 className="text-xl font-bold text-white">Barca X Command Center</h1>
              <p className="text-xs text-slate-400">Admin authentication required</p>
            </div>
          </div>

          <label className="mb-2 block text-sm font-medium text-slate-300">Admin Password</label>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="Enter ADMIN_PASSWORD"
            className="mb-4 w-full rounded-xl border border-slate-700 bg-slate-950 px-4 py-3 text-sm text-white outline-none focus:border-[#004d98]"
            required
          />
          {authError && <p className="mb-4 text-sm text-rose-400">{authError}</p>}

          <button
            type="submit"
            disabled={loggingIn}
            className="flex w-full items-center justify-center gap-2 rounded-xl bg-gradient-to-r from-[#004d98] to-[#a50044] px-4 py-3 text-sm font-semibold text-white transition hover:opacity-95 disabled:opacity-50"
          >
            {loggingIn ? <Loader2 className="h-4 w-4 animate-spin" /> : <KeyRound className="h-4 w-4" />}
            Sign In
          </button>
        </form>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
      {/* Header */}
      <header className="mb-6 flex flex-col gap-4 rounded-2xl border border-slate-800/80 bg-slate-900/75 p-5 shadow-xl backdrop-blur sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-3.5">
          <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-gradient-to-br from-[#004d98] via-[#4a266a] to-[#a50044] text-lg font-black text-[#edbb00] shadow-lg">
            FCB
          </div>
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="text-xl font-bold tracking-tight text-white">Barca X Auto-Poster</h1>
              <span
                className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-semibold ${
                  config.bot_enabled
                    ? "bg-emerald-500/15 text-emerald-300 ring-1 ring-emerald-500/30"
                    : "bg-amber-500/15 text-amber-300 ring-1 ring-amber-500/30"
                }`}
              >
                <span className={`h-1.5 w-1.5 rounded-full ${config.bot_enabled ? "bg-emerald-400" : "bg-amber-400"}`} />
                {config.bot_enabled ? (config.dry_run ? "DRY RUN" : "LIVE AUTO") : "PAUSED"}
              </span>
            </div>
            <p className="mt-0.5 text-xs text-slate-400">
              {barcaMatches[0]
                ? `Next/Live Barça: [${barcaMatches[0].competition}] ${barcaMatches[0].homeTeam} ${
                    barcaMatches[0].homeScore ?? "vs"
                  } ${barcaMatches[0].awayScore ?? ""} ${barcaMatches[0].awayTeam} (${barcaMatches[0].detail})`
                : "No immediate Barça match in 48h window — off-day banter & rival slander active"}
            </p>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <button
            onClick={handleQuickToggleBot}
            disabled={savingConfig || !auth.databaseConfigured}
            className={`inline-flex items-center gap-1.5 rounded-xl px-3.5 py-2 text-xs font-semibold transition ${
              config.bot_enabled
                ? "border border-amber-500/40 bg-amber-500/10 text-amber-200 hover:bg-amber-500/20"
                : "border border-emerald-500/40 bg-emerald-500/15 text-emerald-200 hover:bg-emerald-500/25"
            } disabled:opacity-40`}
          >
            {config.bot_enabled ? <PauseCircle className="h-4 w-4" /> : <PlayCircle className="h-4 w-4" />}
            {config.bot_enabled ? "Pause Bot" : "Resume Bot"}
          </button>

          <button
            onClick={() => void loadAll()}
            disabled={loading}
            className="inline-flex items-center gap-1.5 rounded-xl border border-slate-700 bg-slate-800/80 px-3 py-2 text-xs font-medium text-slate-200 hover:bg-slate-700"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </button>

          {auth.authRequired && (
            <button
              onClick={handleLogout}
              className="inline-flex items-center gap-1.5 rounded-xl border border-slate-700 bg-slate-800/60 px-3 py-2 text-xs font-medium text-slate-300 hover:bg-slate-700"
            >
              <LogOut className="h-3.5 w-3.5" />
              Sign Out
            </button>
          )}
        </div>
      </header>

      {/* Setup Warnings */}
      {(!auth.databaseConfigured || !auth.githubConfigured || !auth.authRequired) && (
        <div className="mb-5 flex flex-col gap-2 rounded-xl border border-amber-500/30 bg-amber-500/10 p-3.5 text-xs text-amber-200">
          {!auth.databaseConfigured && (
            <div className="flex items-center gap-2">
              <AlertTriangle className="h-4 w-4 shrink-0 text-amber-400" />
              <span>
                <strong>DATABASE_URL not set:</strong> Connect a free Neon or Supabase PostgreSQL URL in both Vercel and
                Heroku to share live settings, HT/FT locks, and Gemini API key activity.
              </span>
            </div>
          )}
          {!auth.githubConfigured && (
            <div className="flex items-center gap-2">
              <AlertTriangle className="h-4 w-4 shrink-0 text-amber-400" />
              <span>
                <strong>GITHUB_REPO / GITHUB_TOKEN not set:</strong> Add them in Vercel to upload and manage player
                images directly in GitHub.
              </span>
            </div>
          )}
          {!auth.authRequired && (
            <div className="flex items-center gap-2">
              <AlertTriangle className="h-4 w-4 shrink-0 text-amber-400" />
              <span>
                <strong>ADMIN_PASSWORD not set:</strong> Fine for localhost, but always set <code>ADMIN_PASSWORD</code>{" "}
                in Vercel Environment Variables before deploying publicly.
              </span>
            </div>
          )}
        </div>
      )}

      {/* Toast Banner */}
      {banner && (
        <div
          className={`mb-5 flex items-center gap-2 rounded-xl border px-4 py-3 text-sm ${
            banner.type === "ok"
              ? "border-emerald-500/40 bg-emerald-500/15 text-emerald-200"
              : "border-rose-500/40 bg-rose-500/15 text-rose-200"
          }`}
        >
          {banner.type === "ok" ? <CheckCircle2 className="h-4 w-4 shrink-0" /> : <AlertTriangle className="h-4 w-4 shrink-0" />}
          <span>{banner.text}</span>
        </div>
      )}

      {/* Navigation Tabs */}
      <nav className="mb-6 flex flex-wrap gap-2">
        {[
          { id: "activity" as const, label: "Activity & API Keys", icon: Activity },
          { id: "controls" as const, label: "Posting Behavior", icon: Sliders },
          { id: "images" as const, label: `GitHub Images (${images.length})`, icon: ImageIcon },
        ].map((item) => {
          const Icon = item.icon;
          const active = tab === item.id;
          return (
            <button
              key={item.id}
              onClick={() => setTab(item.id)}
              className={`inline-flex items-center gap-2 rounded-xl px-4 py-2.5 text-sm font-semibold transition ${
                active
                  ? "bg-gradient-to-r from-[#004d98] to-[#a50044] text-white shadow-lg"
                  : "border border-slate-800 bg-slate-900/70 text-slate-300 hover:bg-slate-800"
              }`}
            >
              <Icon className="h-4 w-4" />
              {item.label}
            </button>
          );
        })}
      </nav>

      {/* ==================== TAB 1: ACTIVITY & GEMINI KEY TELEMETRY ==================== */}
      {tab === "activity" && (
        <div className="space-y-6">
          {/* KPI Cards */}
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
            <div className="rounded-2xl border border-slate-800 bg-slate-900/75 p-4">
              <p className="text-xs text-slate-400">Live Posts Today (UTC)</p>
              <p className="mt-1 text-2xl font-bold text-white">
                {stats.postsTodayLive} <span className="text-sm font-normal text-slate-400">/ {config.max_posts_per_day}</span>
              </p>
            </div>

            <div className="rounded-2xl border border-emerald-500/25 bg-slate-900/75 p-4">
              <p className="flex items-center gap-1.5 text-xs text-emerald-300">
                <KeyRound className="h-3.5 w-3.5" /> Gemini Key #1 (Primary)
              </p>
              <p className="mt-1 text-2xl font-bold text-white">
                {stats.primaryKeyCount} <span className="text-xs font-medium text-emerald-400">({primaryPct}%)</span>
              </p>
            </div>

            <div className="rounded-2xl border border-amber-500/25 bg-slate-900/75 p-4">
              <p className="flex items-center gap-1.5 text-xs text-amber-300">
                <KeyRound className="h-3.5 w-3.5" /> Gemini Key #2 (Fallback)
              </p>
              <p className="mt-1 text-2xl font-bold text-white">
                {stats.fallbackKeyCount}{" "}
                <span className="text-xs font-medium text-amber-400">
                  ({totalKeyCalls > 0 ? 100 - primaryPct : 0}%)
                </span>
              </p>
            </div>

            <div className="rounded-2xl border border-slate-800 bg-slate-900/75 p-4">
              <p className="flex items-center gap-1.5 text-xs text-sky-300">
                <Flame className="h-3.5 w-3.5" /> HT / FT Triggers
              </p>
              <p className="mt-1 text-2xl font-bold text-white">
                {stats.halftimeCount} <span className="text-xs font-normal text-slate-400">HT</span> · {stats.fulltimeCount}{" "}
                <span className="text-xs font-normal text-slate-400">FT</span>
              </p>
            </div>

            <div className="col-span-2 rounded-2xl border border-slate-800 bg-slate-900/75 p-4 sm:col-span-1">
              <p className="text-xs text-slate-400">Est. X API Spend</p>
              <p className="mt-1 text-2xl font-bold text-[#edbb00]">${stats.estimatedXSpendUsd.toFixed(2)}</p>
              <p className="text-[11px] text-slate-500">{stats.totalLivePosts} live posts × $0.015</p>
            </div>
          </div>

          {/* 1-Click Live Preview Generator */}
          <div className="rounded-2xl border border-slate-800 bg-slate-900/75 p-5">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <h2 className="flex items-center gap-2 text-base font-bold text-white">
                  <Sparkles className="h-4 w-4 text-[#edbb00]" />
                  Instant Dry-Run Preview (Tests Dual Gemini Keys Live)
                </h2>
                <p className="text-xs text-slate-400">
                  Generates a sample post using your current settings, live ESPN match context, and GitHub images without
                  spending X credits.
                </p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <select
                  value={previewArchetype}
                  onChange={(e) => setPreviewArchetype(e.target.value)}
                  className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-200"
                >
                  {ARCHETYPES.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.label}
                    </option>
                  ))}
                </select>
                <select
                  value={previewTrigger}
                  onChange={(e) => setPreviewTrigger(e.target.value)}
                  className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-slate-200"
                >
                  <option value="manual">Manual Trigger</option>
                  <option value="halftime">Simulate Half-Time</option>
                  <option value="fulltime">Simulate Full-Time</option>
                </select>
                <button
                  onClick={handleGeneratePreview}
                  disabled={generatingPreview || !auth.geminiPreviewReady}
                  className="inline-flex items-center gap-1.5 rounded-xl bg-gradient-to-r from-[#004d98] to-[#a50044] px-4 py-2 text-xs font-semibold text-white transition hover:opacity-95 disabled:opacity-40"
                >
                  {generatingPreview ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5" />}
                  Generate Preview
                </button>
              </div>
            </div>

            {previewResult && (
              <div className="mt-4 rounded-xl border border-slate-700 bg-slate-950/80 p-4">
                <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
                  <span
                    className={`rounded-full px-2.5 py-0.5 font-semibold ${
                      previewResult.gemini_key_used === "primary"
                        ? "bg-emerald-500/20 text-emerald-300"
                        : "bg-amber-500/20 text-amber-300"
                    }`}
                  >
                    {previewResult.gemini_key_used === "primary" ? "Gemini Key #1 (Primary)" : "Gemini Key #2 (Fallback)"}
                  </span>
                  <span className="rounded-full bg-slate-800 px-2.5 py-0.5 text-slate-300">{previewResult.gemini_model}</span>
                  <span className="rounded-full bg-slate-800 px-2.5 py-0.5 text-slate-300">
                    {previewResult.text.length}/280 chars
                  </span>
                  {previewResult.selected_image_ids.length > 0 && (
                    <span className="rounded-full bg-sky-500/20 px-2.5 py-0.5 text-sky-300">
                      Images: {previewResult.selected_image_ids.join(", ")}
                    </span>
                  )}
                </div>
                <p className="whitespace-pre-wrap text-sm leading-relaxed text-white">{previewResult.text}</p>
                {previewResult.gemini_fallback_reason && (
                  <p className="mt-2 text-xs text-amber-300">
                    Fallback reason: {previewResult.gemini_fallback_reason}
                  </p>
                )}
              </div>
            )}
          </div>

          {/* Live Match Radar */}
          {(barcaMatches.length > 0 || rivalMatches.length > 0) && (
            <div className="grid gap-4 md:grid-cols-2">
              <div className="rounded-2xl border border-slate-800 bg-slate-900/75 p-4">
                <h3 className="mb-2 text-xs font-bold uppercase tracking-wider text-sky-400">Barça Match Radar</h3>
                {barcaMatches.length === 0 ? (
                  <p className="text-xs text-slate-400">No Barça match in the immediate 48-hour scoreboard window.</p>
                ) : (
                  <div className="space-y-2">
                    {barcaMatches.map((m, idx) => (
                      <div key={idx} className="flex items-center justify-between rounded-xl bg-slate-950/70 px-3 py-2 text-xs">
                        <span className="font-medium text-white">
                          [{m.competition}] {m.homeTeam} {m.homeScore ?? "vs"} {m.awayScore ?? ""} {m.awayTeam}
                        </span>
                        <span className="rounded-md bg-[#004d98]/40 px-2 py-0.5 text-[11px] text-sky-200">{m.detail}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              <div className="rounded-2xl border border-slate-800 bg-slate-900/75 p-4">
                <h3 className="mb-2 text-xs font-bold uppercase tracking-wider text-rose-400">Rival Slander Radar</h3>
                {rivalMatches.length === 0 ? (
                  <p className="text-xs text-slate-400">No tracked rival matches in window.</p>
                ) : (
                  <div className="space-y-2">
                    {rivalMatches.slice(0, 4).map((m, idx) => (
                      <div key={idx} className="flex items-center justify-between rounded-xl bg-slate-950/70 px-3 py-2 text-xs">
                        <span className="text-slate-200">
                          [{m.competition}] {m.homeTeam} {m.homeScore ?? "vs"} {m.awayScore ?? ""} {m.awayTeam}
                        </span>
                        <span className="rounded-md bg-slate-800 px-2 py-0.5 text-[11px] text-slate-300">{m.detail}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Post & API Key Activity Feed */}
          <div className="rounded-2xl border border-slate-800 bg-slate-900/75 p-5">
            <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <h2 className="text-base font-bold text-white">Posting & Gemini API Key Activity</h2>
                <p className="text-xs text-slate-400">Every live post and dry-run with Primary vs Fallback key tracking</p>
              </div>
              <div className="flex flex-wrap gap-1.5">
                {[
                  { id: "all" as const, label: `All (${posts.length})` },
                  { id: "live" as const, label: "Live X" },
                  { id: "match" as const, label: "HT / FT" },
                  { id: "fallback" as const, label: "Fallback Key" },
                  { id: "dry" as const, label: "Dry Runs" },
                ].map((f) => (
                  <button
                    key={f.id}
                    onClick={() => setActivityFilter(f.id)}
                    className={`rounded-lg px-2.5 py-1.5 text-xs font-medium transition ${
                      activityFilter === f.id
                        ? "bg-[#004d98] text-white"
                        : "bg-slate-800 text-slate-300 hover:bg-slate-700"
                    }`}
                  >
                    {f.label}
                  </button>
                ))}
              </div>
            </div>

            {filteredPosts.length === 0 ? (
              <div className="rounded-xl border border-dashed border-slate-800 py-12 text-center text-sm text-slate-400">
                No activity entries match this filter yet.
              </div>
            ) : (
              <div className="space-y-3">
                {filteredPosts.map((post) => (
                  <div
                    key={post.id}
                    className="rounded-xl border border-slate-800 bg-slate-950/75 p-4 transition hover:border-slate-700"
                  >
                    <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                      <div className="flex flex-wrap items-center gap-1.5 text-[11px] font-semibold">
                        {/* Gemini Key Telemetry Pill */}
                        <span
                          className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 ${
                            post.gemini_key_used === "fallback"
                              ? "bg-amber-500/20 text-amber-300 ring-1 ring-amber-500/40"
                              : "bg-emerald-500/15 text-emerald-300 ring-1 ring-emerald-500/30"
                          }`}
                        >
                          <KeyRound className="h-3 w-3" />
                          {post.gemini_key_used === "fallback" ? "Key #2 FALLBACK" : "Key #1 PRIMARY"}
                        </span>

                        {/* Trigger Badge */}
                        <span
                          className={`rounded-full px-2.5 py-0.5 uppercase ${
                            post.trigger_type === "halftime" || post.trigger_type === "fulltime"
                              ? "bg-[#a50044]/30 text-rose-200 ring-1 ring-rose-500/40"
                              : "bg-slate-800 text-slate-300"
                          }`}
                        >
                          {post.trigger_type}
                        </span>

                        <span className="rounded-full bg-[#004d98]/30 px-2.5 py-0.5 text-sky-200">{post.archetype}</span>

                        {post.gemini_model && (
                          <span className="rounded-full bg-slate-800/90 px-2 py-0.5 font-normal text-slate-400">
                            {post.gemini_model}
                          </span>
                        )}

                        {post.dry_run && (
                          <span className="rounded-full bg-purple-500/20 px-2 py-0.5 text-purple-300">DRY RUN</span>
                        )}
                      </div>

                      <div className="flex items-center gap-2 text-xs text-slate-400">
                        <span title={post.created_at_utc}>{formatRelativeTime(post.created_at_utc)}</span>
                        {!post.dry_run && post.tweet_id && post.tweet_id !== "dry-run" && (
                          <a
                            href={`https://x.com/i/web/status/${post.tweet_id}`}
                            target="_blank"
                            rel="noreferrer"
                            className="inline-flex items-center gap-1 text-sky-400 hover:underline"
                          >
                            View on X <ExternalLink className="h-3 w-3" />
                          </a>
                        )}
                      </div>
                    </div>

                    <p className="whitespace-pre-wrap text-sm leading-relaxed text-slate-100">{post.text}</p>

                    <div className="mt-2.5 flex flex-wrap items-center gap-3 text-[11px] text-slate-400">
                      {post.match_label && <span>Match: {post.match_label}</span>}
                      {post.image_ids.length > 0 && (
                        <span className="text-sky-300">Images: {post.image_ids.join(", ")}</span>
                      )}
                      {post.gemini_fallback_reason && (
                        <span className="text-amber-300">Primary fail reason: {post.gemini_fallback_reason}</span>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {/* ==================== TAB 2: POSTING BEHAVIOR CONTROLS ==================== */}
      {tab === "controls" && (
        <div className="space-y-6">
          <div className="grid gap-6 lg:grid-cols-2">
            {/* Core Switches & Match Triggers */}
            <div className="space-y-4 rounded-2xl border border-slate-800 bg-slate-900/75 p-5">
              <h2 className="text-base font-bold text-white">Master Controls & Live Match Triggers</h2>

              {[
                {
                  key: "bot_enabled" as const,
                  title: "Enable Automatic Posting",
                  desc: "Master kill switch. Pause anytime without touching Heroku Scheduler.",
                },
                {
                  key: "dry_run" as const,
                  title: "Dry-Run Mode (No X Credits Used)",
                  desc: "Generates posts & logs them to Activity without actually publishing to X.",
                },
                {
                  key: "auto_post_halftime" as const,
                  title: "Instant Half-Time Trigger (HT)",
                  desc: "Fires a spicy reaction immediately when ESPN detects Barça Half-Time.",
                },
                {
                  key: "auto_post_fulltime" as const,
                  title: "Instant Full-Time Trigger (FT + Post-Match News)",
                  desc: "Pulls breaking Barça post-match news right after the whistle & posts immediately.",
                },
                {
                  key: "auto_post_prematch" as const,
                  title: "Pre-Match Delusion Window",
                  desc: "Switches to overconfident hype & scoreline predictions before kickoff.",
                },
                {
                  key: "auto_post_rival_drama" as const,
                  title: "Rival Drama Focus",
                  desc: "Targets Real Madrid / Atlético / UCL rivals when they play or drop points.",
                },
              ].map((item) => (
                <label
                  key={item.key}
                  className="flex cursor-pointer items-center justify-between gap-4 rounded-xl border border-slate-800 bg-slate-950/60 p-3.5 hover:border-slate-700"
                >
                  <div>
                    <p className="text-sm font-semibold text-white">{item.title}</p>
                    <p className="text-xs text-slate-400">{item.desc}</p>
                  </div>
                  <input
                    type="checkbox"
                    checked={Boolean(config[item.key])}
                    onChange={(e) => setConfig({ ...config, [item.key]: e.target.checked })}
                    className="h-5 w-5 accent-[#a50044]"
                  />
                </label>
              ))}
            </div>

            {/* Cadence, Chaos, Model & Custom Directive */}
            <div className="space-y-5 rounded-2xl border border-slate-800 bg-slate-900/75 p-5">
              <h2 className="text-base font-bold text-white">Cadence, Chaos Level & Gemini Tuning</h2>

              <div className="grid grid-cols-3 gap-3">
                <div>
                  <label className="mb-1 block text-xs font-medium text-slate-300">Max Posts / Day</label>
                  <input
                    type="number"
                    min={1}
                    max={15}
                    value={config.max_posts_per_day}
                    onChange={(e) => setConfig({ ...config, max_posts_per_day: Number(e.target.value) })}
                    className="w-full rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-xs font-medium text-slate-300">Quiet Cooldown (m)</label>
                  <input
                    type="number"
                    min={15}
                    max={720}
                    value={config.min_minutes_between_posts}
                    onChange={(e) => setConfig({ ...config, min_minutes_between_posts: Number(e.target.value) })}
                    className="w-full rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-xs font-medium text-slate-300">Match Cooldown (m)</label>
                  <input
                    type="number"
                    min={10}
                    max={240}
                    value={config.matchday_cooldown_minutes}
                    onChange={(e) => setConfig({ ...config, matchday_cooldown_minutes: Number(e.target.value) })}
                    className="w-full rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white"
                  />
                </div>
              </div>

              <div>
                <div className="mb-1 flex justify-between text-xs">
                  <span className="font-medium text-slate-300">Image Attachment Ratio</span>
                  <span className="font-bold text-[#edbb00]">{Math.round(config.image_post_ratio * 100)}%</span>
                </div>
                <input
                  type="range"
                  min={0}
                  max={100}
                  value={Math.round(config.image_post_ratio * 100)}
                  onChange={(e) => setConfig({ ...config, image_post_ratio: Number(e.target.value) / 100 })}
                  className="w-full accent-[#004d98]"
                />
              </div>

              <div>
                <label className="mb-1.5 block text-xs font-medium text-slate-300">Banter Chaos Level</label>
                <div className="grid grid-cols-3 gap-2">
                  {(["balanced", "spicy", "unhinged"] as const).map((lvl) => (
                    <button
                      key={lvl}
                      type="button"
                      onClick={() => setConfig({ ...config, chaos_level: lvl })}
                      className={`rounded-xl py-2 text-xs font-bold uppercase tracking-wide transition ${
                        config.chaos_level === lvl
                          ? "bg-gradient-to-r from-[#004d98] to-[#a50044] text-white"
                          : "border border-slate-700 bg-slate-950 text-slate-400 hover:text-white"
                      }`}
                    >
                      {lvl}
                    </button>
                  ))}
                </div>
              </div>

              <div className="grid gap-3 sm:grid-cols-2">
                <div>
                  <label className="mb-1 block text-xs font-medium text-slate-300">Primary Gemini Model</label>
                  <select
                    value={config.gemini_model}
                    onChange={(e) => setConfig({ ...config, gemini_model: e.target.value })}
                    className="w-full rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white"
                  >
                    <option value="gemini-3.5-flash-lite">gemini-3.5-flash-lite (Fastest / Best Free Tier)</option>
                    <option value="gemini-4-hydrogen">gemini-4-hydrogen (Auto-falls back on 503)</option>
                  </select>
                </div>

                <label className="flex cursor-pointer items-center justify-between rounded-xl border border-slate-700 bg-slate-950 px-3 py-2">
                  <span className="text-xs font-medium text-slate-200">Google Search Grounding</span>
                  <input
                    type="checkbox"
                    checked={config.enable_google_search}
                    onChange={(e) => setConfig({ ...config, enable_google_search: e.target.checked })}
                    className="h-4 w-4 accent-[#004d98]"
                  />
                </label>
              </div>

              <div>
                <label className="mb-1 block text-xs font-medium text-slate-300">
                  Custom Banter Directive (Optional Weekly Agenda)
                </label>
                <textarea
                  rows={3}
                  value={config.custom_directive}
                  onChange={(e) => setConfig({ ...config, custom_directive: e.target.value })}
                  placeholder="e.g. Push Pedri Ballon d'Or agenda this week and roast Real Madrid's defense..."
                  className="w-full rounded-xl border border-slate-700 bg-slate-950 p-3 text-sm text-white outline-none focus:border-[#004d98]"
                />
              </div>
            </div>
          </div>

          {/* 24-Hour UTC Posting Slots */}
          <div className="rounded-2xl border border-slate-800 bg-slate-900/75 p-5">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <div>
                <h3 className="text-sm font-bold text-white">Off-Day Posting Hours (UTC)</h3>
                <p className="text-xs text-slate-400">
                  Selected: {config.post_hours_utc.length} slots/day (IST = UTC + 5:30). HT/FT match triggers always fire
                  immediately outside these hours.
                </p>
              </div>
            </div>
            <div className="grid grid-cols-6 gap-2 sm:grid-cols-12">
              {Array.from({ length: 24 }, (_, hour) => {
                const selected = config.post_hours_utc.includes(hour);
                return (
                  <button
                    key={hour}
                    type="button"
                    onClick={() => {
                      const next = selected
                        ? config.post_hours_utc.filter((h) => h !== hour)
                        : [...config.post_hours_utc, hour].sort((a, b) => a - b);
                      setConfig({ ...config, post_hours_utc: next });
                    }}
                    className={`rounded-lg py-2 text-xs font-semibold transition ${
                      selected
                        ? "bg-[#004d98] text-white ring-1 ring-sky-400/50"
                        : "bg-slate-950 text-slate-400 hover:bg-slate-800"
                    }`}
                  >
                    {String(hour).padStart(2, "0")}:00
                  </button>
                );
              })}
            </div>
          </div>

          {/* Enabled Archetypes */}
          <div className="rounded-2xl border border-slate-800 bg-slate-900/75 p-5">
            <h3 className="mb-3 text-sm font-bold text-white">Enabled Post Archetypes</h3>
            <div className="grid gap-2.5 sm:grid-cols-2 lg:grid-cols-3">
              {ARCHETYPES.map((arch) => {
                const checked = config.enabled_archetypes.includes(arch.id);
                return (
                  <label
                    key={arch.id}
                    className={`flex cursor-pointer items-start gap-3 rounded-xl border p-3 transition ${
                      checked ? "border-[#004d98]/70 bg-[#004d98]/15" : "border-slate-800 bg-slate-950/60"
                    }`}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={(e) => {
                        const next = e.target.checked
                          ? [...config.enabled_archetypes, arch.id]
                          : config.enabled_archetypes.filter((x) => x !== arch.id);
                        setConfig({ ...config, enabled_archetypes: next });
                      }}
                      className="mt-0.5 h-4 w-4 accent-[#a50044]"
                    />
                    <div>
                      <p className="text-xs font-bold text-white">{arch.label}</p>
                      <p className="text-[11px] text-slate-400">{arch.desc}</p>
                    </div>
                  </label>
                );
              })}
            </div>
          </div>

          {/* Save Bar */}
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-slate-800 bg-slate-900/90 p-4">
            <span className="text-xs text-slate-400">
              {settingsUpdatedAt ? `Last saved: ${new Date(settingsUpdatedAt).toLocaleString()}` : "Not yet saved to DB"}
            </span>
            <button
              onClick={() => void handleSaveConfig()}
              disabled={savingConfig || !auth.databaseConfigured}
              className="inline-flex items-center gap-2 rounded-xl bg-gradient-to-r from-[#004d98] to-[#a50044] px-6 py-2.5 text-sm font-bold text-white shadow-lg transition hover:opacity-95 disabled:opacity-40"
            >
              {savingConfig ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
              Save Behavior Settings
            </button>
          </div>
        </div>
      )}

      {/* ==================== TAB 3: GITHUB IMAGE LIBRARY & UPLOADER ==================== */}
      {tab === "images" && (
        <div className="space-y-6">
          {/* Upload Form */}
          <form
            onSubmit={handleUploadImage}
            className="rounded-2xl border border-slate-800 bg-slate-900/75 p-5 shadow-xl"
          >
            <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
              <div>
                <h2 className="flex items-center gap-2 text-base font-bold text-white">
                  <Upload className="h-4 w-4 text-[#edbb00]" />
                  Upload Player / Rival Image to GitHub
                </h2>
                <p className="text-xs text-slate-400">
                  Commits directly to{" "}
                  <code className="text-sky-300">
                    {githubMeta.repo || "GITHUB_REPO"}/{githubMeta.branch}/assets/images/
                  </code>{" "}
                  and updates <code>assets/catalog.json</code> — Heroku picks it up automatically with zero redeploy!
                </p>
              </div>
            </div>

            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <div>
                <label className="mb-1 block text-xs font-medium text-slate-300">Category</label>
                <select
                  value={uploadCategory}
                  onChange={(e) => {
                    const cat = e.target.value as "barca" | "rivals" | "managers" | "memes";
                    setUploadCategory(cat);
                    if (cat === "barca") setUploadClub("FC Barcelona");
                    else if (cat === "rivals") setUploadClub("Real Madrid");
                    else if (cat === "managers") setUploadClub("FC Barcelona");
                    else setUploadClub("Football Reaction");
                  }}
                  className="w-full rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white"
                >
                  <option value="barca">Barça Players (barca/)</option>
                  <option value="rivals">Rival Players (rivals/)</option>
                  <option value="managers">Managers (managers/)</option>
                  <option value="memes">Memes / Ref / Stadium (memes/)</option>
                </select>
              </div>

              <div>
                <label className="mb-1 block text-xs font-medium text-slate-300">Player / Subject Name *</label>
                <input
                  type="text"
                  value={uploadSubject}
                  onChange={(e) => setUploadSubject(e.target.value)}
                  placeholder="e.g. Lamine Yamal, Vinicius Jr"
                  className="w-full rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white"
                  required
                />
              </div>

              <div>
                <label className="mb-1 block text-xs font-medium text-slate-300">Club</label>
                <input
                  type="text"
                  value={uploadClub}
                  onChange={(e) => setUploadClub(e.target.value)}
                  placeholder="e.g. FC Barcelona, Real Madrid"
                  className="w-full rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white"
                />
              </div>

              <div>
                <label className="mb-1 block text-xs font-medium text-slate-300">Mood / Emotion</label>
                <select
                  value={uploadMood}
                  onChange={(e) => setUploadMood(e.target.value)}
                  className="w-full rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white"
                >
                  {MOOD_PRESETS.map((m) => (
                    <option key={m} value={m}>
                      {m}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            <div className="mt-4 grid gap-4 sm:grid-cols-2">
              <div>
                <label className="mb-1 block text-xs font-medium text-slate-300">
                  Custom Description for Gemini (Optional)
                </label>
                <input
                  type="text"
                  value={uploadDescription}
                  onChange={(e) => setUploadDescription(e.target.value)}
                  placeholder="e.g. Lamine Yamal doing the 304 celebration after scoring"
                  className="w-full rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white"
                />
              </div>

              <div>
                <label className="mb-1 block text-xs font-medium text-slate-300">Image File (JPG / PNG / WebP, max 4.8 MB)</label>
                <input
                  type="file"
                  accept="image/jpeg,image/png,image/webp"
                  onChange={(e) => setUploadFile(e.target.files?.[0] || null)}
                  className="w-full rounded-xl border border-slate-700 bg-slate-950 px-3 py-1.5 text-xs text-slate-300 file:mr-3 file:rounded-lg file:border-0 file:bg-[#004d98] file:px-3 file:py-1 file:text-xs file:font-semibold file:text-white"
                  required
                />
              </div>
            </div>

            <div className="mt-4 flex justify-end">
              <button
                type="submit"
                disabled={uploading || !githubMeta.configured}
                className="inline-flex items-center gap-2 rounded-xl bg-gradient-to-r from-[#004d98] to-[#a50044] px-5 py-2.5 text-sm font-bold text-white shadow-lg transition hover:opacity-95 disabled:opacity-40"
              >
                {uploading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
                {uploading ? "Committing to GitHub..." : "Upload to GitHub"}
              </button>
            </div>
          </form>

          {/* Image Library Grid */}
          <div className="rounded-2xl border border-slate-800 bg-slate-900/75 p-5">
            <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
              <h3 className="text-base font-bold text-white">Stored GitHub Images ({filteredImages.length})</h3>
              <div className="flex flex-wrap gap-1.5">
                {["all", "barca", "rivals", "managers", "memes"].map((cat) => (
                  <button
                    key={cat}
                    onClick={() => setImageFilterCat(cat)}
                    className={`rounded-lg px-2.5 py-1 text-xs font-semibold capitalize ${
                      imageFilterCat === cat ? "bg-[#a50044] text-white" : "bg-slate-800 text-slate-300 hover:bg-slate-700"
                    }`}
                  >
                    {cat}
                  </button>
                ))}
              </div>
            </div>

            {filteredImages.length === 0 ? (
              <div className="rounded-xl border border-dashed border-slate-800 py-12 text-center text-sm text-slate-400">
                {githubMeta.configured
                  ? "No images in this category yet. Upload your first Barça or rival photo above!"
                  : "Set GITHUB_REPO and GITHUB_TOKEN in Vercel env vars to browse & upload GitHub images."}
              </div>
            ) : (
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
                {filteredImages.map((img) => (
                  <div
                    key={img.id}
                    className="group overflow-hidden rounded-xl border border-slate-800 bg-slate-950/80 transition hover:border-slate-600"
                  >
                    <div className="relative aspect-[4/3] w-full overflow-hidden bg-slate-900">
                      {/* Authenticated proxy works for both public and private GitHub repos */}
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img
                        src={`/api/images/raw?path=${encodeURIComponent(img.repo_path)}`}
                        alt={img.description}
                        className="h-full w-full object-cover transition duration-300 group-hover:scale-105"
                      />
                      <span className="absolute left-2 top-2 rounded-md bg-slate-950/80 px-2 py-0.5 text-[10px] font-bold uppercase text-sky-300">
                        {img.category}
                      </span>
                    </div>
                    <div className="p-3">
                      <div className="flex items-start justify-between gap-2">
                        <div>
                          <p className="text-sm font-bold text-white">{img.subject}</p>
                          <p className="text-[11px] text-slate-400">
                            {img.club} · <span className="text-[#edbb00]">{img.mood}</span>
                          </p>
                        </div>
                        <button
                          onClick={() => void handleDeleteImage(img.relative_path)}
                          disabled={deletingPath === img.relative_path}
                          title="Delete from GitHub"
                          className="rounded-lg p-1.5 text-slate-400 transition hover:bg-rose-500/20 hover:text-rose-300"
                        >
                          {deletingPath === img.relative_path ? (
                            <Loader2 className="h-3.5 w-3.5 animate-spin" />
                          ) : (
                            <Trash2 className="h-3.5 w-3.5" />
                          )}
                        </button>
                      </div>
                      <p className="mt-1.5 truncate font-mono text-[10px] text-slate-500" title={img.id}>
                        id: {img.id}
                      </p>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
