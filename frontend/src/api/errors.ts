/** Turn FastAPI / axios error payloads into a human-readable string. */

interface ValidationItem {
  loc?: (string | number)[];
  msg?: string;
}

export function formatApiError(err: unknown): string {
  if (!err || typeof err !== 'object' || !('response' in err)) {
    return err instanceof Error ? err.message : String(err);
  }

  const data = (err as { response?: { data?: { detail?: unknown } } }).response
    ?.data;
  const detail = data?.detail;

  if (typeof detail === 'string') return detail;

  if (Array.isArray(detail)) {
    const lines = detail
      .map((item: ValidationItem) => {
        const field = item.loc?.slice(-1)[0];
        const label =
          field === 'password'
            ? '密码'
            : field === 'email'
              ? '邮箱'
              : field === 'display_name'
                ? '昵称'
                : String(field ?? '字段');
        return `${label}: ${item.msg ?? '校验失败'}`;
      })
      .filter(Boolean);
    if (lines.length) return lines.join('；');
  }

  return (err as { message?: string }).message || '请求失败';
}
