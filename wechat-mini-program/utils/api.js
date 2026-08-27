const { API_BASE, WECHAT_CLOUD_LOGIN_FUNCTION } = require('./config');
const { clearSession, getToken } = require('./storage');

class ApiError extends Error {
  constructor(message, status = 0) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

function errorMessage(body, status) {
  if (body && body.error && body.error.message) return String(body.error.message);
  if (body && typeof body.detail === 'string') return body.detail;
  return `请求失败 (${status || '网络错误'})`;
}

function request(path, options = {}) {
  const token = getToken();
  return new Promise((resolve, reject) => {
    wx.request({
      url: `${API_BASE}${path}`,
      method: options.method || 'GET',
      data: options.data,
      timeout: 60000,
      header: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      success(response) {
        const body = response.data;
        if (response.statusCode >= 200 && response.statusCode < 300 && body && body.ok) {
          resolve(body.data || {});
          return;
        }
        if (response.statusCode === 401) clearSession();
        reject(new ApiError(errorMessage(body, response.statusCode), response.statusCode));
      },
      fail(error) {
        reject(new ApiError(error.errMsg || '网络连接失败'));
      },
    });
  });
}

function callWechatLogin(payload) {
  return new Promise((resolve, reject) => {
    if (!wx.cloud || typeof wx.cloud.callFunction !== 'function') {
      reject(new ApiError('当前微信版本不支持云登录，请升级微信后重试'));
      return;
    }
    wx.cloud.callFunction({
      name: WECHAT_CLOUD_LOGIN_FUNCTION,
      data: payload,
      success(response) {
        const body = response && response.result;
        if (body && body.ok) {
          resolve(body.data || {});
          return;
        }
        reject(new ApiError(errorMessage(body, 502), 502));
      },
      fail(error) {
        reject(new ApiError(error.errMsg || '微信云登录失败'));
      },
    });
  });
}

module.exports = {
  ApiError,
  auth: {
    wechatLogin: callWechatLogin,
    webviewTicket: () => request('/auth/webview-ticket', { method: 'POST', data: {} }),
  },
};
