export class ApiError extends Error {
  constructor(message: string, public readonly status: number) {
    super(message);
    this.name = 'ApiError';
  }
}

export async function api<T>(path: string, body?: unknown, method = body === undefined ? 'GET' : 'POST', signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, {
    method, signal,
    headers: body === undefined ? undefined : {'Content-Type': 'application/json'},
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  let result;
  try { result = text ? JSON.parse(text) : null; }
  catch { throw new ApiError(`服务返回了无效响应（${response.status}），请检查本地服务。`, response.status); }
  if (!response.ok) {
    const details = Array.isArray(result?.details) ? result.details : [];
    throw new ApiError([result?.error || `请求失败（${response.status}）`, ...details].join(' · '), response.status);
  }
  return result as T;
}
