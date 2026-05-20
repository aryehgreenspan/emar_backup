/**
 * Flask runs with SERVER_NAME (e.g. app.emarvault.com), so Werkzeug only matches
 * requests whose Host header matches. Use FLASK_INTERNAL_URL with that hostname
 * (see app service network alias in docker-compose.yml).
 */
export function flaskInternalUrl(path: string): string {
  const base = (
    process.env.FLASK_INTERNAL_URL || "http://app.emarvault.com:5000"
  ).replace(/\/$/, "");
  const suffix = path.startsWith("/") ? path : `/${path}`;
  return `${base}${suffix}`;
}

export function flaskInternalPost(
  path: string,
  body: unknown,
): Promise<Response> {
  return fetch(flaskInternalUrl(path), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}
