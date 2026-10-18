let token = "";
export function setToken(value: string) {
  token = value;
}

export async function api<T>(
  path: string,
  method = "GET",
  body?: unknown,
): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method,
    headers:
      method === "GET"
        ? {}
        : { "Content-Type": "application/json", "X-Studio-Token": token },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    const error = await response
      .json()
      .catch(() => ({ detail: "Connection failed. Try again." }));
    const detail = Array.isArray(error.detail)
      ? error.detail.map((item: { msg: string }) => item.msg).join(" ")
      : error.detail;
    throw new Error(detail || `Request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export function upload<T>(
  file: File,
  onProgress: (percent: number) => void,
): Promise<T> {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("POST", "/api/runs");
    request.setRequestHeader("X-Studio-Token", token);
    request.upload.onprogress = (event) => {
      if (event.lengthComputable)
        onProgress(Math.round((event.loaded / event.total) * 100));
    };
    request.onerror = () =>
      reject(new Error("Upload interrupted. Check the local server."));
    request.onload = () => {
      try {
        const result = JSON.parse(request.responseText);
        if (request.status >= 400)
          reject(new Error(result.detail || "Upload failed."));
        else resolve(result);
      } catch {
        reject(new Error("The server returned an unreadable response."));
      }
    };
    const form = new FormData();
    form.append("file", file);
    request.send(form);
  });
}
