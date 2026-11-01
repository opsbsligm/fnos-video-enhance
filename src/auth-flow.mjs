// 两处入口共享结果处理；取消、失败不得报告授权成功。
export function pickerPaths(result) {
  if (!result) return [];
  const paths = Array.isArray(result.data) ? result.data.filter(p => typeof p === 'string' && p.startsWith('/')) : [];
  if (result.code !== undefined && result.code !== 0) {
    if (/cancel|取消/i.test(result.msg || '')) return [];
    throw new Error(result.msg === 'Operation failed' ? '目录选择未完成（可能已取消），未收到成功授权结果。' : result.msg || '飞牛目录授权失败');
  }
  return paths;
}
export function authState(cryptoApi) {
  const bytes = new Uint8Array(32);
  cryptoApi.getRandomValues(bytes);
  return Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('');
}
export function callbackPaths(result) {
  if (result?.status === 'cancel') return [];
  if (result?.status !== 'success' || result?.error) throw new Error('飞牛目录授权失败');
  if (result.appName !== 'video-enhance' || result.method !== 'pickSharedFile') throw new Error('授权返回的应用或操作不匹配');
  return Array.isArray(result.path) ? result.path : [];
}
