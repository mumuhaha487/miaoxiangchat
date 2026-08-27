const cloud = require('wx-server-sdk');
const crypto = require('crypto');
const https = require('https');
const { URL } = require('url');

cloud.init({ env: cloud.DYNAMIC_CURRENT_ENV });

function text(value, maxLength) {
  return String(value || '').slice(0, maxLength);
}

function canonicalPayload(values) {
  return values.map((value) => {
    const normalized = String(value);
    return `${Buffer.byteLength(normalized, 'utf8')}:${normalized}`;
  }).join('|');
}

function signature(secret, payload) {
  const canonical = canonicalPayload([
    payload.app_id,
    payload.open_id,
    payload.union_id,
    payload.timestamp,
    payload.nonce,
    payload.device_id,
    payload.device_name,
  ]);
  return crypto.createHmac('sha256', secret).update(canonical, 'utf8').digest('hex');
}

function postJson(target, payload) {
  return new Promise((resolve, reject) => {
    const url = new URL(target);
    if (url.protocol !== 'https:') {
      reject(new Error('微信登录后端必须使用 HTTPS'));
      return;
    }
    const body = Buffer.from(JSON.stringify(payload), 'utf8');
    const request = https.request({
      protocol: url.protocol,
      hostname: url.hostname,
      port: url.port || 443,
      path: `${url.pathname}${url.search}`,
      method: 'POST',
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
        'Content-Length': body.length,
      },
    }, (response) => {
      const chunks = [];
      let size = 0;
      response.on('data', (chunk) => {
        size += chunk.length;
        if (size > 1024 * 1024) {
          request.destroy(new Error('微信登录后端响应过大'));
          return;
        }
        chunks.push(chunk);
      });
      response.on('end', () => {
        try {
          const result = JSON.parse(Buffer.concat(chunks).toString('utf8'));
          if (response.statusCode < 200 || response.statusCode >= 300) {
            reject(new Error(result.detail || '微信登录后端拒绝请求'));
            return;
          }
          resolve(result);
        } catch (_error) {
          reject(new Error('微信登录后端响应无效'));
        }
      });
    });
    request.setTimeout(15000, () => request.destroy(new Error('微信登录后端连接超时')));
    request.on('error', reject);
    request.end(body);
  });
}

exports.main = async (event) => {
  try {
    const context = cloud.getWXContext();
    const bridgeSecret = String(process.env.WECHAT_CLOUD_BRIDGE_SECRET || '');
    const backendUrl = String(process.env.MUMU_BACKEND_URL || '');
    if (bridgeSecret.length < 32) throw new Error('微信云登录桥接尚未配置');
    if (!backendUrl.startsWith('https://')) throw new Error('微信云登录后端尚未配置');
    if (!context.APPID || !context.OPENID) throw new Error('无法读取微信身份');

    const payload = {
      app_id: text(context.APPID, 64),
      open_id: text(context.OPENID, 128),
      union_id: text(context.UNIONID, 128),
      timestamp: Math.floor(Date.now() / 1000),
      nonce: crypto.randomBytes(24).toString('hex'),
      device_id: text(event && event.device_id, 256),
      device_name: text(event && event.device_name, 120) || '微信小程序',
    };
    payload.signature = signature(bridgeSecret, payload);
    return await postJson(backendUrl, payload);
  } catch (error) {
    return {
      ok: false,
      error: { message: error && error.message ? error.message : '微信登录失败' },
    };
  }
};
