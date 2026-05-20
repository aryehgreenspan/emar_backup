/**
 * Flask runs with SERVER_NAME (e.g. app.emarvault.com), so Werkzeug only matches
 * requests whose Host header matches. Connect to the app service by Docker DNS
 * (http://app:5000) and send Host from SERVER_NAME / FLASK_INTERNAL_HOST.
 */
function flaskInternalHost(): string {
  const raw =
    process.env.FLASK_INTERNAL_HOST ||
    process.env.SERVER_NAME ||
    "app.emarvault.com";
  return raw.split(":")[0]!;
}

export function flaskInternalUrl(path: string): string {
  const base = (process.env.FLASK_INTERNAL_URL || "http://app:5000").replace(
    /\/$/,
    "",
  );
  const suffix = path.startsWith("/") ? path : `/${path}`;
  return `${base}${suffix}`;
}

export function flaskInternalPost(
  path: string,
  body: unknown,
): Promise<Response> {
  return fetch(flaskInternalUrl(path), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Host: flaskInternalHost(),
    },
    body: JSON.stringify(body),
  });
}
