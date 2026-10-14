// Fetch helpers. Every state-changing request carries X-Finance-UI: 1, which the server
// requires (a cross-site form cannot send it).

const UI_HEADER = { "X-Finance-UI": "1" };

async function handle(response) {
  let data;
  try {
    data = await response.json();
  } catch (e) {
    data = { error: `The server answered ${response.status} without JSON.` };
  }
  if (!response.ok) {
    const err = new Error(data.error || `The server answered ${response.status}.`);
    err.status = response.status;
    throw err;
  }
  return data;
}

export async function getJSON(url) {
  return handle(await fetch(url, { headers: UI_HEADER, cache: "no-store" }));
}

export async function postJSON(url, body) {
  return handle(await fetch(url, {
    method: "POST", cache: "no-store",
    headers: { ...UI_HEADER, "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  }));
}

export async function postForm(url, formData) {
  return handle(await fetch(url, { method: "POST", headers: UI_HEADER, body: formData }));
}

export function qs(params) {
  const q = new URLSearchParams();
  for (const [key, value] of Object.entries(params || {})) {
    if (value !== undefined && value !== null && value !== "") q.set(key, String(value));
  }
  return q.toString();
}

const FINISHED = new Set(["succeeded", "failed", "timeout", "error"]);

/** Poll a background job until it finishes, calling onUpdate with each snapshot. */
export async function followJob(job, onUpdate) {
  let current = job;
  onUpdate(current);
  while (!FINISHED.has(current.status)) {
    await new Promise((resolve) => setTimeout(resolve, 1000));
    current = await getJSON(`/api/jobs/${encodeURIComponent(current.id)}`);
    onUpdate(current);
  }
  return current;
}

export function isFinished(job) {
  return FINISHED.has(job.status);
}
