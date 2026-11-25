import crypto from "crypto";
import { NextResponse } from "next/server";
import { isAuthenticated } from "@/lib/auth";
import { getLiveTweetIds, updateMetricsByTweetId } from "@/lib/db";

function percentEncode(str: string): string {
  return encodeURIComponent(str).replace(/[!'()*]/g, (c) => `%${c.charCodeAt(0).toString(16).toUpperCase()}`);
}

function buildOAuth1Header(params: {
  method: string;
  baseUrl: string;
  queryParams: Record<string, string>;
  consumerKey: string;
  consumerSecret: string;
  token: string;
  tokenSecret: string;
}): string {
  const oauthParams: Record<string, string> = {
    oauth_consumer_key: params.consumerKey,
    oauth_nonce: crypto.randomBytes(16).toString("hex"),
    oauth_signature_method: "HMAC-SHA1",
    oauth_timestamp: String(Math.floor(Date.now() / 1000)),
    oauth_token: params.token,
    oauth_version: "1.0",
  };

  const allParams = { ...params.queryParams, ...oauthParams };
  const paramString = Object.keys(allParams)
    .sort()
    .map((k) => `${percentEncode(k)}=${percentEncode(allParams[k])}`)
    .join("&");

  const baseString = [
    params.method.toUpperCase(),
    percentEncode(params.baseUrl),
    percentEncode(paramString),
  ].join("&");

  const signingKey = `${percentEncode(params.consumerSecret)}&${percentEncode(params.tokenSecret)}`;
  const signature = crypto.createHmac("sha1", signingKey).update(baseString).digest("base64");

  const headerParams: Record<string, string> = { ...oauthParams, oauth_signature: signature };
  const headerBody = Object.keys(headerParams)
    .sort()
    .map((k) => `${percentEncode(k)}="${percentEncode(headerParams[k])}"`)
    .join(", ");
  return `OAuth ${headerBody}`;
}

export async function POST() {
  if (!(await isAuthenticated())) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const consumerKey = process.env.X_API_KEY?.trim() || "";
  const consumerSecret = process.env.X_API_SECRET?.trim() || "";
  const token = process.env.X_ACCESS_TOKEN?.trim() || "";
  const tokenSecret = process.env.X_ACCESS_TOKEN_SECRET?.trim() || "";

  if (!consumerKey || !consumerSecret || !token || !tokenSecret) {
    return NextResponse.json(
      {
        error:
          "Add X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, and X_ACCESS_TOKEN_SECRET to Vercel Environment Variables (or run `python run_post.py --sync-metrics` on Heroku).",
      },
      { status: 400 }
    );
  }

  try {
    const ids = await getLiveTweetIds(50);
    if (ids.length === 0) {
      return NextResponse.json({ synced: 0, message: "No live tweets in DB yet." });
    }

    const baseUrl = "https://api.x.com/2/tweets";
    const queryParams = {
      ids: ids.join(","),
      "tweet.fields": "public_metrics",
    };

    const authHeader = buildOAuth1Header({
      method: "GET",
      baseUrl,
      queryParams,
      consumerKey,
      consumerSecret,
      token,
      tokenSecret,
    });

    const qs = new URLSearchParams(queryParams).toString();
    const res = await fetch(`${baseUrl}?${qs}`, {
      method: "GET",
      headers: { Authorization: authHeader },
      cache: "no-store",
    });

    if (!res.ok) {
      const errText = await res.text();
      return NextResponse.json(
        { error: `X API metrics sync failed (${res.status}): ${errText.slice(0, 220)}` },
        { status: res.status }
      );
    }

    const payload = (await res.json()) as {
      data?: Array<{
        id: string;
        public_metrics?: {
          impression_count?: number;
          like_count?: number;
          reply_count?: number;
          retweet_count?: number;
          quote_count?: number;
        };
      }>;
    };

    let synced = 0;
    for (const item of payload.data || []) {
      const m = item.public_metrics || {};
      await updateMetricsByTweetId(item.id, {
        impressions: Number(m.impression_count || 0),
        likes: Number(m.like_count || 0),
        replies: Number(m.reply_count || 0),
        retweets: Number(m.retweet_count || 0),
        quotes: Number(m.quote_count || 0),
      });
      synced += 1;
    }

    return NextResponse.json({ synced });
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Failed to sync X metrics" },
      { status: 500 }
    );
  }
}
