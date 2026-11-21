// Minimal MailerLite subscribe proxy for hybtretfs.com.
// Reads MAILERLITE_TOKEN from the environment. Never echoes it.
// No npm dependencies. Deploy as a Vercel serverless function (see docs/MAILERLITE_SETUP.md).

const ALLOWED_ORIGINS = [
  "https://hybtretfs.com",
  "https://www.hybtretfs.com",
];

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const MAILERLITE_URL = "https://connect.mailerlite.com/api/subscribers";
const MAX_BODY_BYTES = 4096;
const RATE_WINDOW_MS = 10 * 60 * 1000;
const RATE_MAX = 8;

// Process-local limiter. Good enough for light abuse on a small hobby function;
// it resets when the instance is recycled and is not shared across regions.
const hits = new Map();

function cors(res, origin) {
  if (ALLOWED_ORIGINS.includes(origin)) {
    res.setHeader("Access-Control-Allow-Origin", origin);
    res.setHeader("Vary", "Origin");
  }
  res.setHeader("Access-Control-Allow-Methods", "POST, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type, Accept");
  res.setHeader("Access-Control-Max-Age", "86400");
}

function clientIp(req) {
  const forwarded = req.headers["x-forwarded-for"];
  if (typeof forwarded === "string" && forwarded.trim()) {
    return forwarded.split(",")[0].trim();
  }
  return req.socket?.remoteAddress || "unknown";
}

function tooMany(ip) {
  const now = Date.now();
  const recent = (hits.get(ip) || []).filter((t) => now - t < RATE_WINDOW_MS);
  recent.push(now);
  hits.set(ip, recent);
  return recent.length > RATE_MAX;
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    req.on("data", (chunk) => {
      size += chunk.length;
      if (size > MAX_BODY_BYTES) {
        reject(Object.assign(new Error("body too large"), { code: "TOO_LARGE" }));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on("end", () => {
      const raw = Buffer.concat(chunks).toString("utf8") || "{}";
      try {
        resolve(JSON.parse(raw));
      } catch (err) {
        reject(Object.assign(new Error("invalid json"), { code: "BAD_JSON" }));
      }
    });
    req.on("error", reject);
  });
}

function json(res, status, body) {
  res.statusCode = status;
  res.setHeader("Content-Type", "application/json; charset=utf-8");
  res.end(JSON.stringify(body));
}

module.exports = async function handler(req, res) {
  const origin = req.headers.origin || "";
  cors(res, origin);

  if (req.method === "OPTIONS") {
    res.statusCode = ALLOWED_ORIGINS.includes(origin) ? 204 : 403;
    res.end();
    return;
  }

  if (req.method !== "POST") {
    json(res, 405, { message: "Method not allowed" });
    return;
  }

  if (!ALLOWED_ORIGINS.includes(origin)) {
    json(res, 403, { message: "Forbidden" });
    return;
  }

  const token = process.env.MAILERLITE_TOKEN;
  if (!token) {
    json(res, 503, { message: "Signup is temporarily unavailable." });
    return;
  }

  const ip = clientIp(req);
  if (tooMany(ip)) {
    json(res, 429, { message: "Too many requests. Please try again later." });
    return;
  }

  let payload;
  try {
    payload = await readBody(req);
  } catch (err) {
    const status = err.code === "TOO_LARGE" ? 413 : 400;
    json(res, status, { message: "Invalid request." });
    return;
  }

  const email = typeof payload.email === "string" ? payload.email.trim() : "";
  if (!EMAIL_RE.test(email) || email.length > 254) {
    json(res, 400, { message: "Please enter a valid email address." });
    return;
  }

  const body = { email };

  if (typeof payload.name === "string") {
    const name = payload.name.trim().slice(0, 200);
    if (name) body.name = name;
  }

  // Always add only to the HYBTR Weekly Newsletter group. Ignore any
  // client-supplied group ids so a caller cannot enroll people elsewhere.
  const allowedGroup = process.env.MAILERLITE_GROUP || "187947608914789971";
  if (/^\d+$/.test(allowedGroup)) {
    body.groups = [allowedGroup];
  }

  if (payload.fields && typeof payload.fields === "object" && !Array.isArray(payload.fields)) {
    const fields = {};
    for (const [key, value] of Object.entries(payload.fields)) {
      if (typeof key === "string" && /^[A-Za-z0-9_]{1,40}$/.test(key) && typeof value === "string") {
        fields[key] = value.slice(0, 200);
      }
      if (Object.keys(fields).length >= 8) break;
    }
    if (Object.keys(fields).length) body.fields = fields;
  }

  let upstream;
  try {
    upstream = await fetch(MAILERLITE_URL, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
        Authorization: "Bearer " + token,
      },
      body: JSON.stringify(body),
    });
  } catch (err) {
    json(res, 502, { message: "Signup is temporarily unavailable." });
    return;
  }

  let data = {};
  try {
    data = await upstream.json();
  } catch (err) {
    data = {};
  }

  // Pass through success / "already subscribed" without leaking upstream headers or the token.
  if (upstream.ok || upstream.status === 409) {
    const message =
      (data && typeof data.message === "string" && data.message) ||
      (upstream.status === 409 ? "Already subscribed." : "Subscribed.");
    json(res, upstream.status, { message });
    return;
  }

  json(res, upstream.status >= 400 && upstream.status < 600 ? upstream.status : 502, {
    message: "Something went wrong. Please try again.",
  });
};
